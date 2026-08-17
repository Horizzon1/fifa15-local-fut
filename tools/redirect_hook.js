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
  var address = Module.findExportByName(moduleName, exportName);
  if (address === null) return false;

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
  var address = Module.findExportByName('ws2_32.dll', exportName);
  if (address === null) return false;

  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        var sockaddr = args[1];
        if (sockaddr.isNull()) return;
        var family = sockaddr.readU16();
        if (family !== 2 /* AF_INET */) return;

        // sin_port is network byte order.
        var hi = sockaddr.add(2).readU8();
        var lo = sockaddr.add(3).readU8();
        var port = (hi << 8) | lo;
        var ip = ipv4ToString(sockaddr.add(4));

        if (ip === LOCALHOST) { stats.connectPassed++; return; }

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

// ws2_32 re-exports the resolver entry points; hook both it and the CRT copy so
// whichever one FIFA links against is covered.
['ws2_32.dll', 'wship6.dll'].forEach(function (mod) {
  try { hookGetAddrInfo(mod, 'getaddrinfo', false); } catch (e) {}
  try { hookGetAddrInfo(mod, 'GetAddrInfoW', true); } catch (e) {}
});
hookConnect('connect');
hookConnect('WSAConnect');

emit('hook-ready', { pointerSize: Process.pointerSize, arch: Process.arch });
