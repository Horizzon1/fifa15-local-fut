/*
 * Capture FIFA 15's own internal diagnostics.
 *
 * The game reports "Unable to connect to the EA servers" while opening zero TCP
 * connections, so the decision is made inside the client before any networking.
 * EA's DirtySDK and Blaze SDK log heavily through NetPrintf, which on Windows
 * ends up at OutputDebugStringA/W. Retail builds usually leave those calls in.
 *
 * Capturing them should say, in EA's own words, why the client considers itself
 * offline — which is the root cause rather than another symptom.
 *
 * Also hooks WSAStartup and the DirtySDK-style UPnP path so the network-init
 * ordering is visible alongside the log lines.
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

var lines = 0;

function hookDebugString(name, wide) {
  var address = resolveExport('kernel32.dll', name);
  if (!address) { address = resolveExport('KernelBase.dll', name); }
  if (!address) { emit('absent', { api: name }); return; }

  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        if (args[0].isNull()) return;
        var text = wide ? args[0].readUtf16String() : args[0].readCString();
        if (!text) return;
        text = text.replace(/[\r\n]+$/, '');
        if (!text.length) return;
        lines++;
        emit('debug', { text: text });
      } catch (e) {}
    }
  });
  emit('hooked', { api: 'kernel32!' + name });
}

hookDebugString('OutputDebugStringA', false);
hookDebugString('OutputDebugStringW', true);

// Winsock init tells us whether the online stack was started at all.
var wsaStartup = resolveExport('ws2_32.dll', 'WSAStartup');
if (wsaStartup) {
  Interceptor.attach(wsaStartup, {
    onLeave: function (retval) {
      emit('wsa-startup', { result: retval.toInt32() });
    }
  });
  emit('hooked', { api: 'ws2_32!WSAStartup' });
}

// WSAGetLastError values around the failure are often more honest than the UI.
var getLastError = resolveExport('ws2_32.dll', 'WSAGetLastError');
if (getLastError) {
  var reported = {};
  Interceptor.attach(getLastError, {
    onLeave: function (retval) {
      var code = retval.toInt32();
      if (code === 0) return;
      reported[code] = (reported[code] || 0) + 1;
      if (reported[code] <= 3) emit('wsa-error', { code: code });
    }
  });
  emit('hooked', { api: 'ws2_32!WSAGetLastError' });
}

rpc.exports = { lines: function () { return lines; } };
emit('debug-tracer-ready', {});
