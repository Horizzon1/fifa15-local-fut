/*
 * Does FIFA 15 actually read cl.ini, and which config keys does it look up?
 *
 * Adding FUT_DIRECT_BOOT / LoadFUTSkipBlaze / DirectBootFUT to cl.ini changed
 * nothing, which has two very different explanations:
 *   a) the game never opens cl.ini, so config-via-file is not a channel at all
 *   b) it reads it, but those keys do not bypass the session requirement
 *
 * This settles which, by watching the file opens and the private-profile
 * (INI) lookups.
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

var seen = {};

// --- file opens: is cl.ini touched at all? --------------------------------
[['kernel32.dll', 'CreateFileW', true], ['kernel32.dll', 'CreateFileA', false]].forEach(function (entry) {
  var address = resolveExport(entry[0], entry[1]);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        if (args[0].isNull()) return;
        var path = entry[2] ? args[0].readUtf16String() : args[0].readCString();
        if (!path) return;
        if (!/cl\.ini|\.ini$|osdk|debug/i.test(path)) return;
        if (seen['f' + path]) return;
        seen['f' + path] = true;
        emit('config-file-open', { path: path });
      } catch (e) {}
    }
  });
  emit('hooked', { api: entry[1] });
});

// --- GetPrivateProfileString: the classic INI reader -----------------------
[['GetPrivateProfileStringW', true], ['GetPrivateProfileStringA', false],
 ['GetPrivateProfileIntW', true], ['GetPrivateProfileIntA', false]].forEach(function (entry) {
  var address = resolveExport('kernel32.dll', entry[0]);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function (args) {
      try {
        var key = args[1].isNull() ? null
          : (entry[1] ? args[1].readUtf16String() : args[1].readCString());
        if (!key || seen['k' + key]) return;
        seen['k' + key] = true;
        emit('ini-key', { api: entry[0], key: key });
      } catch (e) {}
    }
  });
  emit('hooked', { api: entry[0] });
});

emit('config-tracer-ready', {});
