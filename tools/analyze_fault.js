/*
 * Deep fault analysis for the FIFA 15 boot crash.
 *
 * The earlier pass established WHERE it dies (fifa15.exe+0x3f41916, null read,
 * on a worker thread) and that no network call happens first. This pass answers
 * WHY: it dumps the faulting instruction and registers, disassembles the code
 * around the fault and its caller, and watches the audio stack — the crash lands
 * immediately after a long run of audio-asset loads, so a failed audio init
 * whose error the game never checks is the leading theory.
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

var game = Process.findModuleByName('fifa15.exe');
emit('module', { name: 'fifa15.exe', base: game.base.toString(), size: game.size });

function disassemble(address, count, label) {
  var lines = [];
  var cursor = address;
  for (var i = 0; i < count; i++) {
    try {
      var insn = Instruction.parse(cursor);
      lines.push('+0x' + cursor.sub(game.base).toString(16) + '  ' + insn.mnemonic + ' ' + insn.opStr);
      cursor = insn.next;
    } catch (e) {
      lines.push('<undecodable at ' + cursor + '>');
      break;
    }
  }
  emit('disasm', { label: label, lines: lines });
}

// The fault site and its two callers, from the earlier backtrace.
try { disassemble(game.base.add(0x3f41916), 8, 'fault-site'); } catch (e) {}
try { disassemble(game.base.add(0x3f418e0), 20, 'fault-site-context'); } catch (e) {}
try { disassemble(game.base.add(0x3f41b40), 20, 'caller-3f41b6d'); } catch (e) {}
try { disassemble(game.base.add(0x3f41ae0), 20, 'caller-3f41b13'); } catch (e) {}

// ---------------------------------------------------------------------------
// Audio stack: which backend loads, and does its init fail?
// ---------------------------------------------------------------------------
['XAudio2Create', 'DirectSoundCreate8', 'DirectSoundCreate',
 'waveOutOpen', 'waveOutGetNumDevs'].forEach(function (name) {
  var address = resolveExport(null, name);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter: function () { this.name = name; },
    onLeave: function (retval) {
      emit('audio-api', { api: this.name, result: retval.toInt32(),
                          ok: retval.toInt32() >= 0 });
    }
  });
  emit('hooked', { api: name });
});

// CoCreateInstance is how WASAPI's device enumerator is obtained.
var coCreate = resolveExport('ole32.dll', 'CoCreateInstance');
if (coCreate) {
  Interceptor.attach(coCreate, {
    onLeave: function (retval) {
      var hr = retval.toInt32();
      if (hr < 0) emit('co-create-failed', { hr: '0x' + (hr >>> 0).toString(16) });
    }
  });
  emit('hooked', { api: 'CoCreateInstance' });
}

// Module loads reveal which audio/graphics backends actually engage.
var loadLibraryW = resolveExport('kernel32.dll', 'LoadLibraryW');
if (loadLibraryW) {
  Interceptor.attach(loadLibraryW, {
    onEnter: function (args) {
      try { this.path = args[0].isNull() ? null : args[0].readUtf16String(); } catch (e) {}
    },
    onLeave: function (retval) {
      if (this.path && /audio|xaudio|dsound|wasapi|mmdev|x3d|wine|openal/i.test(this.path)) {
        emit('audio-module', { path: this.path, loaded: !retval.isNull() });
      }
    }
  });
}

// ---------------------------------------------------------------------------
// The fault, with registers.
// ---------------------------------------------------------------------------
Process.setExceptionHandler(function (details) {
  if (details.type !== 'access-violation') return false;

  var context = details.context;
  var report = {
    address: String(details.address),
    rva: '0x' + details.address.sub(game.base).toString(16),
    operation: details.memory ? details.memory.operation : null,
    faultingAddress: details.memory ? String(details.memory.address) : null
  };

  // Which register held the null pointer?
  var registers = {};
  ['rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'rbp', 'rsp',
   'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15'].forEach(function (name) {
    try { registers[name] = String(context[name]); } catch (e) {}
  });
  report.registers = registers;

  try {
    var insn = Instruction.parse(details.address);
    report.instruction = insn.mnemonic + ' ' + insn.opStr;
  } catch (e) {}

  try {
    report.backtrace = Thread.backtrace(context, Backtracer.ACCURATE)
      .slice(0, 20)
      .map(function (addr) {
        var mod = Process.findModuleByAddress(addr);
        return mod ? (mod.name + '+0x' + addr.sub(mod.base).toString(16)) : String(addr);
      });
  } catch (e) {}

  emit('FAULT', report);
  return false;
});

emit('analyzer-ready', {});
