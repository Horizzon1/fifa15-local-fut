/*
 * Confirm the boot crash is a missing Windows Media Player COM class.
 *
 * Evidence so far: CoCreateInstance returns 0x80040154 REGDB_E_CLASSNOTREG,
 * the game ignores the HRESULT, and immediately does `mov rax,[rcx]` with
 * rcx=0. On this machine wmp.dll is absent and CLSID_WMPlayer is unregistered.
 *
 * Earlier runs logged the failure but not its CLSID, because the process died
 * before the onLeave message flushed. So this logs the CLSID on ENTRY — the
 * last line before the crash names the class the game could not create.
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

function readGuid(pointer) {
  if (!pointer || pointer.isNull()) return null;
  try {
    function hex(value, width) {
      var text = value.toString(16).toUpperCase();
      while (text.length < width) text = '0' + text;
      return text;
    }
    var d1 = pointer.readU32();
    var d2 = pointer.add(4).readU16();
    var d3 = pointer.add(6).readU16();
    var rest = new Uint8Array(pointer.add(8).readByteArray(8));
    var tail = '';
    for (var i = 0; i < 8; i++) {
      tail += hex(rest[i], 2);
      if (i === 1) tail += '-';
    }
    return '{' + hex(d1, 8) + '-' + hex(d2, 4) + '-' + hex(d3, 4) + '-' + tail + '}';
  } catch (e) { return null; }
}

// Classes worth naming when we see them.
var KNOWN = {
  '{CF4CB6E9-3B0E-4B62-9F0E-7CFF96D1D0D9}': 'WMPlayer (Windows Media Player)',
  '{6BF52A52-394A-11D3-B153-00C04F79FAA6}': 'WindowsMediaPlayer control',
  '{E436EBB3-524F-11CE-9F53-0020AF0BA770}': 'FilterGraph (DirectShow)',
  '{E436EBB8-524F-11CE-9F53-0020AF0BA770}': 'FilterGraphNoThread',
  '{BCDE0395-E52F-467C-8E3D-C4579291692E}': 'MMDeviceEnumerator',
  '{4590F811-1D3A-11D0-891F-00AA004B2E24}': 'WbemLocator (WMI)'
};

var lastClsid = null;

['CoCreateInstance', 'CoCreateInstanceEx'].forEach(function (name) {
  var address = resolveExport('ole32.dll', name);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function (args) {
      var clsid = readGuid(args[0]);
      this.clsid = clsid;
      lastClsid = clsid;
      // Log on ENTRY so the record survives a crash inside/after the call.
      emit('attempt', { api: name, clsid: clsid, known: KNOWN[clsid] || null });
    },
    onLeave: function (retval) {
      var hr = retval.toInt32();
      if (hr < 0) {
        emit('FAILED', {
          api: name,
          clsid: this.clsid,
          known: KNOWN[this.clsid] || null,
          hr: '0x' + (hr >>> 0).toString(16).toUpperCase(),
          meaning: hr === -2147221164 ? 'REGDB_E_CLASSNOTREG (class not registered)' : null
        });
      }
    }
  });
  emit('hooked', { api: name });
});

// CoGetClassObject is the other route to the same failure.
var getClassObject = resolveExport('ole32.dll', 'CoGetClassObject');
if (getClassObject) {
  Interceptor.attach(getClassObject, {
    onEnter: function (args) { this.clsid = readGuid(args[0]); lastClsid = this.clsid; },
    onLeave: function (retval) {
      var hr = retval.toInt32();
      if (hr < 0) {
        emit('FAILED', {
          api: 'CoGetClassObject', clsid: this.clsid,
          known: KNOWN[this.clsid] || null,
          hr: '0x' + (hr >>> 0).toString(16).toUpperCase()
        });
      }
    }
  });
}

var game = Process.findModuleByName('fifa15.exe');
Process.setExceptionHandler(function (details) {
  if (details.type !== 'access-violation') return false;
  emit('FAULT', {
    rva: '0x' + details.address.sub(game.base).toString(16),
    rcx: String(details.context.rcx),
    lastClsidAttempted: lastClsid,
    known: KNOWN[lastClsid] || null
  });
  return false;
});

emit('ready', {});
