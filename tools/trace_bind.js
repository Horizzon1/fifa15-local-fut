/*
 * Why does FIFA 15 bind a TCP socket and then close it without connecting?
 *
 * Call stacks show: socket(SOCK_STREAM) -> ioctlsocket(non-blocking) -> bind()
 * -> closesocket(), with no connect() anywhere. So the abort happens at or just
 * after bind.
 *
 * The obvious suspect is bind failing. And there is an uncomfortable
 * possibility worth ruling out first: this project's own server binds
 * 42127/42131/8110/8111/44130 on "::" (dual-stack, all interfaces). If DirtySDK
 * wants a fixed local port in that set, our own listener would be the thing
 * blocking the game — an own goal.
 *
 * This logs every bind with its address, port, and result, plus the matching
 * WSAGetLastError when it fails.
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
  return null;
}

function sockaddrInfo(pointer) {
  try {
    if (!pointer || pointer.isNull()) return null;
    var family = pointer.readU16();
    var port = (pointer.add(2).readU8() << 8) | pointer.add(3).readU8();
    if (family === 2) {
      var b = new Uint8Array(pointer.add(4).readByteArray(4));
      return { family: 'AF_INET', ip: b[0] + '.' + b[1] + '.' + b[2] + '.' + b[3], port: port };
    }
    if (family === 23) return { family: 'AF_INET6', port: port };
    return { family: family, port: port };
  } catch (e) { return null; }
}

var lastErrorFn = resolveExport('ws2_32.dll', 'WSAGetLastError');
var getLastError = lastErrorFn ? new NativeFunction(lastErrorFn, 'int', []) : null;

function errorName(code) {
  var names = {
    10013: 'WSAEACCES (permission denied)',
    10048: 'WSAEADDRINUSE (address already in use)',
    10049: 'WSAEADDRNOTAVAIL (cannot assign requested address)',
    10022: 'WSAEINVAL',
    10038: 'WSAENOTSOCK',
    10047: 'WSAEAFNOSUPPORT'
  };
  return names[code] || String(code);
}

var bindFn = resolveExport('ws2_32.dll', 'bind');
if (bindFn) {
  Interceptor.attach(bindFn, {
    onEnter: function (args) {
      this.handle = args[0].toInt32();
      this.addr = sockaddrInfo(args[1]);
    },
    onLeave: function (retval) {
      var failed = retval.toInt32() !== 0;
      var record = { handle: this.handle, address: this.addr, ok: !failed };
      if (failed && getLastError) {
        var code = getLastError();
        record.error = code;
        record.errorName = errorName(code);
      }
      emit(failed ? 'bind-FAILED' : 'bind', record);
    }
  });
  emit('hooked', { api: 'ws2_32!bind' });
}

// Also record every connect attempt, so its absence stays provable.
['connect', 'WSAConnect'].forEach(function (name) {
  var address = resolveExport('ws2_32.dll', name);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function (args) {
      emit('connect-attempt', { api: name, target: sockaddrInfo(args[1]) });
    }
  });
});

// getsockname after bind reveals the port actually assigned.
var getSockName = resolveExport('ws2_32.dll', 'getsockname');
if (getSockName) {
  var count = 0;
  Interceptor.attach(getSockName, {
    onEnter: function (args) { this.out = args[1]; },
    onLeave: function () {
      if (++count > 10) return;
      emit('getsockname', { address: sockaddrInfo(this.out) });
    }
  });
}

emit('bind-tracer-ready', {});
