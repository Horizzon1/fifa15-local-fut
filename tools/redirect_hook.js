/*
 * FIFA 15 localhost redirect + connection trace.
 *
 * Runs inside fifa15.exe via frida. Two layers:
 *
 *   1. DNS  - getaddrinfo / GetAddrInfoW results for EA hostnames are rewritten
 *             to 127.0.0.1, so the client dials the local server naturally.
 *   2. connect - a safety net. Any connect()/WSAConnect() aimed at a known EA
 *             service port is rewritten to loopback, which also covers hardcoded
 *             IPs and cached DNS.
 *
 * This avoids editing the hosts file, so nothing needs elevation.
 *
 * Every decision is emitted as JSON so the launcher can prove what happened.
 */

'use strict';

var LOCALHOST = '127.0.0.1';

// Hostnames that must resolve to the local server.
var EA_HOST_PATTERNS = [
  /gosredirector/i,
  /\.ea\.com$/i,
  /\.easports\.com$/i,
  /easfc/i,
  /gosca/i,
  /nucleus/i,
  /accounts\.ea/i,
  /signin\.ea/i,
  /gateway\.ea/i
];

// Ports the local server owns. Anything dialled here goes to loopback even if
// DNS was bypassed. Populated from the launcher via the `ports` RPC.
var REDIRECT_PORTS = {};

// Ports that need remapping because the local server could not bind the
// original (e.g. another process already owns it).
var PORT_REMAP = {};

var stats = { dnsRewritten: 0, dnsPassed: 0, connectRewritten: 0, connectPassed: 0 };

/*
 * Resolve an export across frida versions. frida 17 removed
 * Module.findExportByName in favour of per-module lookup and
 * Module.findGlobalExportByName, so try each shape in turn.
 */
function resolveExport(moduleName, exportName) {
  try {
    if (typeof Module.findExportByName === 'function') {
      var legacy = Module.findExportByName(moduleName, exportName);
      if (legacy) return legacy;
    }
  } catch (e) {}
  try {
    var mod = Process.findModuleByName(moduleName);
    if (mod) {
      var viaModule = mod.findExportByName(exportName);
      if (viaModule) return viaModule;
    }
  } catch (e) {}
  try {
    if (typeof Module.findGlobalExportByName === 'function') {
      var global = Module.findGlobalExportByName(exportName);
      if (global) return global;
    }
  } catch (e) {}
  return null;
}

function emit(kind, fields) {
  var payload = { kind: kind, time: Date.now() };
  for (var key in fields) payload[key] = fields[key];
  send(payload);
}

function isEaHost(name) {
  if (!name) return false;
  for (var i = 0; i < EA_HOST_PATTERNS.length; i++) {
    if (EA_HOST_PATTERNS[i].test(name)) return true;
  }
  return false;
}

function ipv4ToString(ptr32) {
  var b = ptr32.readByteArray(4);
  var v = new Uint8Array(b);
  return v[0] + '.' + v[1] + '.' + v[2] + '.' + v[3];
}

function writeLoopback(sinAddrPtr) {
  // 127.0.0.1 in network byte order.
  sinAddrPtr.writeU8(127);
  sinAddrPtr.add(1).writeU8(0);
  sinAddrPtr.add(2).writeU8(0);
  sinAddrPtr.add(3).writeU8(1);
}

/*
 * Walk the addrinfo linked list returned by getaddrinfo and point every IPv4
 * result at loopback.
 *
 * struct addrinfo (Win32, x86):
 *   0  ai_flags, 4 ai_family, 8 ai_socktype, 12 ai_protocol,
 *   16 ai_addrlen, 20 ai_canonname*, 24 ai_addr*, 28 ai_next*
 * struct sockaddr_in: 0 sin_family(2), 2 sin_port(2), 4 sin_addr(4)
 */
function rewriteAddrInfo(resultPtr, hostname) {
  if (resultPtr.isNull()) return 0;
  var head = resultPtr.readPointer();
  var rewritten = 0;
  var node = head;
  var guard = 0;
  while (!node.isNull() && guard++ < 64) {
    var family = node.add(4).readU32();
    var addr = node.add(Process.pointerSize === 8 ? 32 : 24).readPointer();
    if (family === 2 /* AF_INET */ && !addr.isNull()) {
      var before = ipv4ToString(addr.add(4));
      writeLoopback(addr.add(4));
      rewritten++;
      emit('dns-rewrite', { host: hostname, from: before, to: LOCALHOST });
    }
    node = node.add(Process.pointerSize === 8 ? 40 : 28).readPointer();
  }
  return rewritten;
}

function hookGetAddrInfo(moduleName, exportName, wide) {
  var address = resolveExport(moduleName, exportName);
  if (!address) return false;

  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        this.hostname = args[0].isNull()
          ? null
          : (wide ? args[0].readUtf16String() : args[0].readCString());
      } catch (e) {
        this.hostname = null;
      }
      this.resultPtr = args[3];
      this.matched = isEaHost(this.hostname);
    },
    onLeave: function (retval) {
      if (!this.matched) {
        if (this.hostname) stats.dnsPassed++;
        return;
      }
      if (retval.toInt32() !== 0) {
        emit('dns-lookup-failed', { host: this.hostname, code: retval.toInt32() });
        return;
      }
      try {
        var n = rewriteAddrInfo(this.resultPtr, this.hostname);
        if (n > 0) stats.dnsRewritten += n;
      } catch (e) {
        emit('dns-rewrite-error', { host: this.hostname, error: String(e) });
      }
    }
  });
  emit('hook-installed', { api: moduleName + '!' + exportName });
  return true;
}

function hookConnect(exportName) {
  var address = resolveExport('ws2_32.dll', exportName);
  if (!address) {
    emit('hook-missing', { api: 'ws2_32!' + exportName });
    return false;
  }

  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        var sockaddr = args[1];
        if (sockaddr.isNull()) return;
        var family = sockaddr.readU16();

        if (family === 23 /* AF_INET6 */) {
          // The server binds dual-stack, so an IPv6 connect to ::1 reaches it
          // unchanged. Log it rather than dropping it silently.
          var v6port = (sockaddr.add(2).readU8() << 8) | sockaddr.add(3).readU8();
          emit('connect-ipv6', { api: exportName, port: v6port });
          return;
        }
        if (family !== 2 /* AF_INET */) return;

        // sin_port is network byte order.
        var hi = sockaddr.add(2).readU8();
        var lo = sockaddr.add(3).readU8();
        var port = (hi << 8) | lo;
        var ip = ipv4ToString(sockaddr.add(4));

        // Already local: nothing to rewrite, but log it — a silent skip here
        // once hid the fact that the client was reaching us at all.
        if (ip === LOCALHOST) {
          stats.connectPassed++;
          emit('connect-local', { api: exportName, target: ip + ':' + port });
          return;
        }

        var remapped = PORT_REMAP[port];
        if (REDIRECT_PORTS[port] || remapped !== undefined) {
          writeLoopback(sockaddr.add(4));
          if (remapped !== undefined) {
            sockaddr.add(2).writeU8((remapped >> 8) & 0xff);
            sockaddr.add(3).writeU8(remapped & 0xff);
          }
          stats.connectRewritten++;
          emit('connect-rewrite', {
            api: exportName,
            from: ip + ':' + port,
            to: LOCALHOST + ':' + (remapped !== undefined ? remapped : port)
          });
        } else {
          stats.connectPassed++;
          emit('connect-passthrough', { api: exportName, target: ip + ':' + port });
        }
      } catch (e) {
        emit('connect-error', { api: exportName, error: String(e) });
      }
    }
  });
  emit('hook-installed', { api: 'ws2_32!' + exportName });
  return true;
}

rpc.exports = {
  // The launcher tells the hook which ports the local server actually bound.
  ports: function (redirect, remap) {
    REDIRECT_PORTS = {};
    for (var i = 0; i < redirect.length; i++) REDIRECT_PORTS[redirect[i]] = true;
    PORT_REMAP = remap || {};
    emit('ports-configured', { redirect: redirect, remap: PORT_REMAP });
    return true;
  },
  stats: function () { return stats; }
};

/*
 * DnsQueryEx is the one FIFA 15 actually uses. It is asynchronous and returns a
 * DNS_RECORD chain, which is awkward to synthesise — but the query name can be
 * rewritten on the way IN. Pointing it at "localhost" makes Windows itself
 * resolve the lookup to 127.0.0.1, so the client receives a perfectly ordinary
 * answer that happens to be the local server.
 *
 * "localhost" is shorter than every EA hostname, so it always fits in place.
 *
 * DNS_QUERY_REQUEST (x64):
 *   0  ULONG  Version
 *   8  PCWSTR QueryName
 *  16  WORD   QueryType
 *  24  ULONG64 QueryOptions
 */
function hookDnsQueryEx() {
  var address = resolveExport('dnsapi.dll', 'DnsQueryEx');
  if (!address) { emit('hook-missing', { api: 'dnsapi!DnsQueryEx' }); return false; }

  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        var request = args[0];
        if (request.isNull()) return;
        var namePointer = request.add(8).readPointer();
        if (namePointer.isNull()) return;

        var name = namePointer.readUtf16String();
        if (!isEaHost(name)) { stats.dnsPassed++; return; }

        var replacement = 'localhost';
        Memory.protect(namePointer, (replacement.length + 1) * 2, 'rw-');
        namePointer.writeUtf16String(replacement);
        stats.dnsRewritten++;
        emit('dns-rewrite', { api: 'DnsQueryEx', host: name, to: replacement });
      } catch (error) {
        emit('dns-rewrite-error', { api: 'DnsQueryEx', error: String(error) });
      }
    }
  });
  emit('hook-installed', { api: 'dnsapi!DnsQueryEx' });
  return true;
}

/* DnsQuery_W/A take the name as their first argument. */
function hookDnsQuery(exportName, wide) {
  var address = resolveExport('dnsapi.dll', exportName);
  if (!address) return false;
  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        if (args[0].isNull()) return;
        var name = wide ? args[0].readUtf16String() : args[0].readCString();
        if (!isEaHost(name)) { stats.dnsPassed++; return; }
        var replacement = 'localhost';
        if (wide) {
          Memory.protect(args[0], (replacement.length + 1) * 2, 'rw-');
          args[0].writeUtf16String(replacement);
        } else {
          Memory.protect(args[0], replacement.length + 1, 'rw-');
          args[0].writeUtf8String(replacement);
        }
        stats.dnsRewritten++;
        emit('dns-rewrite', { api: exportName, host: name, to: replacement });
      } catch (error) {
        emit('dns-rewrite-error', { api: exportName, error: String(error) });
      }
    }
  });
  emit('hook-installed', { api: 'dnsapi!' + exportName });
  return true;
}

// ws2_32 re-exports the resolver entry points; hook both it and the CRT copy so
// whichever one FIFA links against is covered.
['ws2_32.dll', 'wship6.dll'].forEach(function (mod) {
  try { hookGetAddrInfo(mod, 'getaddrinfo', false); } catch (e) {}
  try { hookGetAddrInfo(mod, 'GetAddrInfoW', true); } catch (e) {}
});
/*
 * ConnectEx.
 *
 * EA's networking uses overlapped sockets, so the actual connect goes through
 * ConnectEx rather than ws2_32!connect. ConnectEx has no export: it is fetched
 * at runtime via WSAIoctl(SIO_GET_EXTENSION_FUNCTION_POINTER, WSAID_CONNECTEX).
 * That is why a TCP socket appeared with no connect() ever being observed.
 *
 * Hooking WSAIoctl lets us grab the pointer the moment it is handed out, then
 * attach to it and rewrite the destination exactly as we do for connect().
 */
var SIO_GET_EXTENSION_FUNCTION_POINTER = 0xC8000006;
var WSAID_CONNECTEX = 'b907a225f3dd60468ee976e58c74063e'; // GUID bytes, little-endian fields
var connectExHooked = false;

function guidHex(pointer) {
  try {
    var bytes = new Uint8Array(pointer.readByteArray(16));
    var out = '';
    for (var i = 0; i < 16; i++) {
      var h = bytes[i].toString(16);
      out += h.length === 1 ? '0' + h : h;
    }
    return out;
  } catch (e) { return null; }
}

function attachConnectEx(address) {
  if (connectExHooked || !address || address.isNull()) return;
  connectExHooked = true;

  // ConnectEx(s, name, namelen, lpSendBuffer, dwSendDataLength, lpdwBytesSent, lpOverlapped)
  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        var sockaddr = args[1];
        if (sockaddr.isNull()) return;
        var family = sockaddr.readU16();
        if (family !== 2 /* AF_INET */) {
          emit('connectex-ipv6', {});
          return;
        }
        var port = (sockaddr.add(2).readU8() << 8) | sockaddr.add(3).readU8();
        var ip = ipv4ToString(sockaddr.add(4));

        if (ip === LOCALHOST) {
          emit('connectex-local', { target: ip + ':' + port });
          return;
        }

        var remapped = PORT_REMAP[port];
        if (REDIRECT_PORTS[port] || remapped !== undefined) {
          writeLoopback(sockaddr.add(4));
          if (remapped !== undefined) {
            sockaddr.add(2).writeU8((remapped >> 8) & 0xff);
            sockaddr.add(3).writeU8(remapped & 0xff);
          }
          stats.connectRewritten++;
          emit('connectex-rewrite', {
            from: ip + ':' + port,
            to: LOCALHOST + ':' + (remapped !== undefined ? remapped : port)
          });
        } else {
          emit('connectex-passthrough', { target: ip + ':' + port });
        }
      } catch (error) {
        emit('connectex-error', { error: String(error) });
      }
    }
  });
  emit('hook-installed', { api: 'mswsock!ConnectEx (via WSAIoctl)' });
}

var wsaIoctl = resolveExport('ws2_32.dll', 'WSAIoctl');
if (wsaIoctl) {
  Interceptor.attach(wsaIoctl, {
    onEnter: function (args) {
      this.code = args[1].toInt32() >>> 0;
      this.inBuffer = args[2];
      this.outBuffer = args[4];
    },
    onLeave: function () {
      if (this.code !== SIO_GET_EXTENSION_FUNCTION_POINTER) return;
      try {
        var guid = guidHex(this.inBuffer);
        if (guid !== WSAID_CONNECTEX) return;
        attachConnectEx(this.outBuffer.readPointer());
      } catch (error) {
        emit('wsaioctl-error', { error: String(error) });
      }
    }
  });
  emit('hook-installed', { api: 'ws2_32!WSAIoctl' });
}

/*
 * DNS is left alone on purpose. spring14.gosredirector.ea.com still resolves
 * (to 159.153.51.19); the service behind it is simply gone. Letting resolution
 * succeed and redirecting at connect time is far more robust than trying to
 * synthesise DNS answers.
 */
emit('dns-policy', { note: 'resolution left intact; redirect happens at connect/ConnectEx' });
hookConnect('connect');
hookConnect('WSAConnect');

emit('hook-ready', { pointerSize: Process.pointerSize, arch: Process.arch });
