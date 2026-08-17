/*
 * Wide network tracer for FIFA 15.
 *
 * The narrow hook set (getaddrinfo / GetAddrInfoW / connect / WSAConnect) saw
 * nothing at all, yet the game reported "Unable to connect to the EA servers".
 * EA's DirtySDK is known to implement its own resolver over raw UDP rather than
 * going through the Winsock name APIs, so this traces every plausible path:
 * socket creation, UDP sends (DNS queries), TCP connects, and the WinINet /
 * WinHTTP / DnsApi entry points.
 *
 * Nothing is filtered — the point is to find out what the client actually uses.
 */

'use strict';

function emit(kind, fields) {
  var payload = { kind: kind };
  for (var key in fields) payload[key] = fields[key];
  send(payload);
}

function resolveExport(moduleName, exportName) {
  try {
    if (typeof Module.findExportByName === 'function') {
      var legacy = Module.findExportByName(moduleName, exportName);
      if (legacy) return legacy;
    }
  } catch (e) {}
  try {
    var mod = moduleName ? Process.findModuleByName(moduleName) : null;
    if (mod) { var v = mod.findExportByName(exportName); if (v) return v; }
  } catch (e) {}
  try {
    if (typeof Module.findGlobalExportByName === 'function') {
      var g = Module.findGlobalExportByName(exportName);
      if (g) return g;
    }
  } catch (e) {}
  return null;
}

function sockaddrInfo(pointer) {
  try {
    if (!pointer || pointer.isNull()) return null;
    if (pointer.readU16() !== 2) return { family: pointer.readU16() };
    var port = (pointer.add(2).readU8() << 8) | pointer.add(3).readU8();
    var raw = new Uint8Array(pointer.add(4).readByteArray(4));
    return { ip: raw[0] + '.' + raw[1] + '.' + raw[2] + '.' + raw[3], port: port };
  } catch (e) { return null; }
}

/* Pull the queried hostname out of a DNS question section. */
function dnsName(buffer, length) {
  try {
    if (length < 13) return null;
    var bytes = new Uint8Array(buffer.readByteArray(Math.min(length, 256)));
    var offset = 12;
    var parts = [];
    while (offset < bytes.length) {
      var labelLength = bytes[offset];
      if (labelLength === 0 || labelLength > 63) break;
      offset++;
      var label = '';
      for (var i = 0; i < labelLength && offset < bytes.length; i++, offset++) {
        label += String.fromCharCode(bytes[offset]);
      }
      parts.push(label);
    }
    return parts.length ? parts.join('.') : null;
  } catch (e) { return null; }
}

var counts = {};
function bump(key) { counts[key] = (counts[key] || 0) + 1; return counts[key]; }

// --- socket creation -------------------------------------------------------
['socket', 'WSASocketW', 'WSASocketA'].forEach(function (name) {
  var address = resolveExport('ws2_32.dll', name);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function (args) {
      this.type = args[1].toInt32();
      this.proto = args[2].toInt32();
    },
    onLeave: function () {
      if (bump('socket') <= 40) {
        emit('socket', { api: name, type: this.type === 1 ? 'TCP' : (this.type === 2 ? 'UDP' : this.type), proto: this.proto });
      }
    }
  });
  emit('hooked', { api: name });
});

// --- TCP connects ----------------------------------------------------------
['connect', 'WSAConnect'].forEach(function (name) {
  var address = resolveExport('ws2_32.dll', name);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function (args) {
      emit('connect', { api: name, target: sockaddrInfo(args[1]) });
    }
  });
  emit('hooked', { api: name });
});

// --- UDP sends: this is where a DirtySDK DNS query would appear -------------
['sendto', 'WSASendTo'].forEach(function (name) {
  var address = resolveExport('ws2_32.dll', name);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        var destination = name === 'sendto' ? sockaddrInfo(args[4]) : sockaddrInfo(args[6]);
        var buffer = name === 'sendto' ? args[1] : null;
        var length = name === 'sendto' ? args[2].toInt32() : 0;
        var record = { api: name, to: destination };
        if (destination && destination.port === 53 && buffer) {
          record.dnsQuery = dnsName(buffer, length);
        }
        if (bump('sendto') <= 60) emit('udp-send', record);
      } catch (e) {}
    }
  });
  emit('hooked', { api: name });
});

// --- plain send, in case the query goes over TCP ---------------------------
var sendFn = resolveExport('ws2_32.dll', 'send');
if (sendFn) {
  Interceptor.attach(sendFn, {
    onEnter: function (args) {
      if (bump('send') <= 25) {
        try {
          var length = args[2].toInt32();
          var preview = args[1].readByteArray(Math.min(length, 48));
          emit('tcp-send', { bytes: length, head: Array.from(new Uint8Array(preview)).slice(0, 24) });
        } catch (e) {}
      }
    }
  });
  emit('hooked', { api: 'send' });
}

// --- higher level HTTP stacks ---------------------------------------------
[['wininet.dll', 'InternetConnectA'], ['wininet.dll', 'InternetConnectW'],
 ['wininet.dll', 'InternetOpenUrlA'], ['wininet.dll', 'InternetOpenUrlW'],
 ['winhttp.dll', 'WinHttpConnect'], ['dnsapi.dll', 'DnsQuery_A'],
 ['dnsapi.dll', 'DnsQuery_W'], ['dnsapi.dll', 'DnsQueryEx']].forEach(function (entry) {
  var address = resolveExport(entry[0], entry[1]);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function (args) {
      var name = null;
      try {
        name = entry[1].slice(-1) === 'W' ? args[1].readUtf16String() : args[1].readCString();
      } catch (e) {
        try { name = args[0].readCString(); } catch (e2) {}
      }
      emit('http-api', { api: entry[1], target: name });
    }
  });
  emit('hooked', { api: entry[0] + '!' + entry[1] });
});

rpc.exports = { counts: function () { return counts; } };
emit('wide-tracer-ready', {});
