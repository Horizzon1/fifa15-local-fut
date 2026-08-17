/*
 * Identify the COM class FIFA 15 fails to create.
 *
 * The boot crash is a null vtable dereference (mov rax,[rcx] with rcx=0)
 * immediately after CoCreateInstance returns 0x80040154 REGDB_E_CLASSNOTREG.
 * The game never checks the HRESULT. This logs every CoCreateInstance and
 * CoCreateInstanceEx call with its CLSID and IID so the missing class can be
 * named exactly.
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

/* A GUID is {DWORD, WORD, WORD, BYTE[8]} in little-endian on disk. */
function readGuid(pointer) {
  if (pointer.isNull()) return null;
  try {
    var d1 = pointer.readU32();
    var d2 = pointer.add(4).readU16();
    var d3 = pointer.add(6).readU16();
    var rest = new Uint8Array(pointer.add(8).readByteArray(8));
    function hex(value, width) {
      var text = value.toString(16).toUpperCase();
      while (text.length < width) text = '0' + text;
      return text;
    }
    var tail = '';
    for (var i = 0; i < 8; i++) {
      tail += hex(rest[i], 2);
      if (i === 1) tail += '-';
    }
    return '{' + hex(d1, 8) + '-' + hex(d2, 4) + '-' + hex(d3, 4) + '-' + tail + '}';
  } catch (e) {
    return null;
  }
}

var calls = 0;

function hookCreate(name, clsidIndex, iidIndex) {
  var address = resolveExport('ole32.dll', name);
  if (!address) { emit('absent', { api: name }); return; }

  Interceptor.attach(address, {
    onEnter: function (args) {
      this.clsid = readGuid(args[clsidIndex]);
      this.iid = readGuid(args[iidIndex]);
      this.api = name;
    },
    onLeave: function (retval) {
      var hr = retval.toInt32();
      calls++;
      var record = {
        api: this.api,
        clsid: this.clsid,
        iid: this.iid,
        hr: '0x' + (hr >>> 0).toString(16).toUpperCase()
      };
      if (hr < 0) {
        record.FAILED = true;
        emit('com-create-FAILED', record);
      } else {
        emit('com-create', record);
      }
    }
  });
  emit('hooked', { api: name });
}

// CoCreateInstance(rclsid, pUnkOuter, dwClsContext, riid, ppv)
hookCreate('CoCreateInstance', 0, 3);
// CoCreateInstanceEx(rclsid, punkOuter, dwClsCtx, pServerInfo, dwCount, pResults)
hookCreate('CoCreateInstanceEx', 0, 0);

// CoGetClassObject fails the same way for class-factory paths.
var getClassObject = resolveExport('ole32.dll', 'CoGetClassObject');
if (getClassObject) {
  Interceptor.attach(getClassObject, {
    onEnter: function (args) { this.clsid = readGuid(args[0]); },
    onLeave: function (retval) {
      var hr = retval.toInt32();
      if (hr < 0) {
        emit('com-getclass-FAILED', {
          clsid: this.clsid, hr: '0x' + (hr >>> 0).toString(16).toUpperCase()
        });
      }
    }
  });
  emit('hooked', { api: 'CoGetClassObject' });
}

emit('ready', {});
