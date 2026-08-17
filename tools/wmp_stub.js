/*
 * Windows Media Player stub for FIFA 15.
 *
 * Root cause of the boot crash, confirmed by tracing:
 *   FIFA 15 calls CoCreateInstance(CLSID_WindowsMediaPlayer) to build the
 *   ActiveX control it plays its intro/bootflow video through. Windows 11 no
 *   longer ships Windows Media Player, so the call returns 0x80040154
 *   REGDB_E_CLASSNOTREG. The game never checks the HRESULT and immediately
 *   executes `mov rax, qword ptr [rcx]` on the null interface pointer.
 *
 * The supported fix is to install "Windows Media Player Legacy", which needs
 * elevation. This is the unprivileged alternative: when that specific class
 * fails to create, hand the game a minimal COM object instead of NULL.
 *
 * The stub implements IUnknown properly (QueryInterface returns itself, so any
 * interface the game asks for resolves) and answers every other vtable slot
 * with E_NOTIMPL. Old EA code paths that ignore HRESULTs then simply do
 * nothing, which is exactly what we want from an intro video.
 *
 * Nothing is installed or registered system-wide; the object exists only
 * inside the game process for its lifetime.
 */

'use strict';

var CLSID_WINDOWS_MEDIA_PLAYER = '{6BF52A52-394A-11D3-B153-00C04F79FAA6}';
var CLSID_WMPLAYER = '{CF4CB6E9-3B0E-4B62-9F0E-7CFF96D1D0D9}';

var S_OK = 0;
var E_NOTIMPL = 0x80004001 | 0;

// Plenty of room for any interface the game queries.
var VTABLE_SLOTS = 256;

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

// Keep every callback referenced forever; if these are collected the vtable
// starts pointing at freed memory.
var keepAlive = [];
var stubObject = null;
var stubUses = 0;

function buildStub() {
  if (stubObject !== null) return stubObject;

  var object = Memory.alloc(Process.pointerSize * 2);
  var vtable = Memory.alloc(Process.pointerSize * VTABLE_SLOTS);

  // IUnknown::QueryInterface(this, riid, ppvObject)
  var queryInterface = new NativeCallback(function (self, riid, ppv) {
    try { if (ppv && !ppv.isNull()) ppv.writePointer(object); } catch (e) {}
    return S_OK;
  }, 'int32', ['pointer', 'pointer', 'pointer']);

  // IUnknown::AddRef / Release. Never report zero, so nothing tries to free us.
  var addRef = new NativeCallback(function () { return 2; }, 'uint32', ['pointer']);
  var release = new NativeCallback(function () { return 1; }, 'uint32', ['pointer']);

  keepAlive.push(queryInterface, addRef, release);
  vtable.add(0 * Process.pointerSize).writePointer(queryInterface);
  vtable.add(1 * Process.pointerSize).writePointer(addRef);
  vtable.add(2 * Process.pointerSize).writePointer(release);

  // Everything else politely declines. Four pointer parameters is enough for
  // the callback to be called safely under the x64 convention, where the
  // caller cleans up and extra arguments are simply ignored.
  for (var slot = 3; slot < VTABLE_SLOTS; slot++) {
    var notImplemented = new NativeCallback(function () {
      stubUses++;
      return E_NOTIMPL;
    }, 'int32', ['pointer', 'pointer', 'pointer', 'pointer']);
    keepAlive.push(notImplemented);
    vtable.add(slot * Process.pointerSize).writePointer(notImplemented);
  }

  object.writePointer(vtable);
  stubObject = object;
  emit('wmp-stub-built', { slots: VTABLE_SLOTS, object: String(object) });
  return object;
}

function wanted(clsid) {
  return clsid === CLSID_WINDOWS_MEDIA_PLAYER || clsid === CLSID_WMPLAYER;
}

var substitutions = 0;

var coCreateInstance = resolveExport('ole32.dll', 'CoCreateInstance');
if (coCreateInstance) {
  Interceptor.attach(coCreateInstance, {
    onEnter: function (args) {
      this.clsid = readGuid(args[0]);
      this.ppv = args[4];
    },
    onLeave: function (retval) {
      if (!wanted(this.clsid) || retval.toInt32() >= 0) return;
      try {
        var stub = buildStub();
        if (this.ppv && !this.ppv.isNull()) {
          this.ppv.writePointer(stub);
          retval.replace(ptr(S_OK));
          substitutions++;
          emit('wmp-substituted', {
            clsid: this.clsid,
            count: substitutions,
            note: 'Windows Media Player is not installed; supplied an inert stub so the intro video is skipped instead of crashing'
          });
        }
      } catch (e) {
        emit('wmp-substitute-failed', { error: String(e) });
      }
    }
  });
  emit('hooked', { api: 'ole32!CoCreateInstance (WMP stub)' });
}

rpc.exports = {
  wmpstats: function () {
    return { substitutions: substitutions, stubCalls: stubUses };
  }
};

emit('wmp-stub-ready', {});
