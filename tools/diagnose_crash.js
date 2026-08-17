/*
 * Crash + network diagnostic for FIFA 15.
 *
 * The game dies ~26s into boot with an access violation, before any TCP
 * connect is observed. This script answers two questions with evidence:
 *
 *   1. Which network APIs does FIFA 15 actually call, and with what?
 *      (A wider net than the redirect hook: name resolution has several
 *      entry points and the game may not use the modern one.)
 *   2. Where exactly does it fault, with a backtrace and module context?
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

var seen = {};
function once(key) {
  if (seen[key]) { seen[key]++; return false; }
  seen[key] = 1;
  return true;
}

// ---------------------------------------------------------------------------
// Name resolution: every entry point the game might plausibly use.
// ---------------------------------------------------------------------------
var NAME_APIS = [
  ['ws2_32.dll', 'gethostbyname', 'ansi'],
  ['ws2_32.dll', 'getaddrinfo', 'ansi'],
  ['ws2_32.dll', 'GetAddrInfoW', 'wide'],
  ['ws2_32.dll', 'GetAddrInfoExW', 'wide'],
  ['ws2_32.dll', 'WSAAsyncGetHostByName', 'ansi'],
  ['ws2_32.dll', 'WSAConnectByNameW', 'wide'],
  ['ws2_32.dll', 'WSAConnectByNameA', 'ansi']
];

NAME_APIS.forEach(function (entry) {
  var moduleName = entry[0], exportName = entry[1], encoding = entry[2];
  var address = resolveExport(moduleName, exportName);
  if (!address) { emit('api-absent', { api: exportName }); return; }

  Interceptor.attach(address, {
    onEnter: function (args) {
      var name = null;
      try {
        // The hostname argument sits at index 0 for the resolver APIs and at
        // index 1 for the WSAConnectByName family.
        var candidate = (exportName.indexOf('ConnectByName') >= 0) ? args[1] : args[0];
        name = candidate.isNull() ? null
          : (encoding === 'wide' ? candidate.readUtf16String() : candidate.readCString());
      } catch (e) {}
      emit('resolve', { api: exportName, host: name });
    }
  });
  emit('hooked', { api: moduleName + '!' + exportName });
});

// ---------------------------------------------------------------------------
// Sockets: log every connect regardless of destination.
// ---------------------------------------------------------------------------
['connect', 'WSAConnect'].forEach(function (exportName) {
  var address = resolveExport('ws2_32.dll', exportName);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        var sockaddr = args[1];
        if (sockaddr.isNull() || sockaddr.readU16() !== 2) return;
        var port = (sockaddr.add(2).readU8() << 8) | sockaddr.add(3).readU8();
        var raw = new Uint8Array(sockaddr.add(4).readByteArray(4));
        emit('connect', {
          api: exportName,
          target: raw[0] + '.' + raw[1] + '.' + raw[2] + '.' + raw[3] + ':' + port
        });
      } catch (e) {}
    }
  });
  emit('hooked', { api: 'ws2_32!' + exportName });
});

// ---------------------------------------------------------------------------
// File opens near the crash often reveal what the game was loading.
// ---------------------------------------------------------------------------
var createFileW = resolveExport('kernel32.dll', 'CreateFileW');
if (createFileW) {
  Interceptor.attach(createFileW, {
    onEnter: function (args) {
      try {
        var path = args[0].isNull() ? null : args[0].readUtf16String();
        if (path && once('file:' + path) && !/\\\\\.\\/.test(path)) {
          emit('file-open', { path: path });
        }
      } catch (e) {}
    }
  });
  emit('hooked', { api: 'kernel32!CreateFileW' });
}

// ---------------------------------------------------------------------------
// The fault itself.
// ---------------------------------------------------------------------------
Process.setExceptionHandler(function (details) {
  var context = details.context;
  var report = {
    type: details.type,
    address: details.address ? details.address.toString() : null,
    memoryOperation: details.memory ? details.memory.operation : null,
    memoryAddress: details.memory ? String(details.memory.address) : null
  };

  try {
    var module = Process.findModuleByAddress(details.address);
    if (module) {
      report.module = module.name;
      report.rva = '0x' + details.address.sub(module.base).toString(16);
      report.moduleBase = module.base.toString();
    }
  } catch (e) {}

  try {
    report.backtrace = Thread.backtrace(context, Backtracer.ACCURATE)
      .slice(0, 16)
      .map(function (addr) {
        var mod = Process.findModuleByAddress(addr);
        return mod ? (mod.name + '+0x' + addr.sub(mod.base).toString(16)) : String(addr);
      });
  } catch (e) {
    report.backtraceError = String(e);
  }

  emit('EXCEPTION', report);

  // Do not swallow it; let the process take its normal course so the
  // observation stays faithful.
  return false;
});

emit('diagnostic-ready', {
  arch: Process.arch,
  pointerSize: Process.pointerSize,
  modules: Process.enumerateModules().length
});
