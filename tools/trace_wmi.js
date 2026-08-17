/*
 * Reveal what FIFA 15 asks WMI for, right before the boot crash.
 *
 * The crash is `mov rax,[rcx]` with rcx=0 on a worker thread that runs a
 * repeating WMI pattern (WbemLocator -> ContextSwitcher -> in-proc handler,
 * nine times). WQL queries and property names travel as BSTRs, so hooking the
 * BSTR allocators exposes the conversation without walking COM vtables.
 *
 * Two things this deliberately does NOT do, both learned the hard way:
 *   - It never hooks an address taken from a backtrace. Those are RETURN
 *     addresses, i.e. mid-instruction, and patching one corrupts the function
 *     and kills the process within seconds.
 *   - It does not assume oleaut32 is loaded at spawn time; the hooks are
 *     installed once the module actually appears.
 */

'use strict';

function emit(kind, fields) {
  var payload = { kind: kind };
  for (var key in fields) payload[key] = fields[key];
  send(payload);
}

var game = Process.findModuleByName('fifa15.exe');
var seen = {};
var bstrCount = 0;

function interesting(text) {
  if (!text || text.length < 4 || text.length > 500) return false;
  return /SELECT|Win32_|ROOT\\|CIMV2|WQL|Adapter|VideoController|Processor|DriverVersion|DeviceID|PNPDevice/i.test(text);
}

function installBstrHooks() {
  var installed = 0;
  ['SysAllocString', 'SysAllocStringLen'].forEach(function (name) {
    var address = null;
    try {
      var mod = Process.findModuleByName('oleaut32.dll');
      if (mod) address = mod.findExportByName(name);
    } catch (e) {}
    if (!address) return;

    Interceptor.attach(address, {
      onEnter: function (args) {
        try {
          var text = args[0].isNull() ? null : args[0].readUtf16String();
          if (!text) return;
          bstrCount++;
          // Device instance paths are logged in order, never deduped: the LAST
          // one before the fault names the device the game chokes on.
          if (/^[A-Z0-9_]+\\/.test(text) && text.length < 300) {
            emit('device', { seq: bstrCount, id: text });
          } else if (interesting(text) && !seen[text]) {
            seen[text] = true;
            emit('bstr', { text: text });
          }
        } catch (e) {}
      }
    });
    installed++;
  });
  if (installed) emit('hooked', { api: 'oleaut32 BSTR allocators', count: installed });
  return installed > 0;
}

// oleaut32 is usually pulled in with COM; poll briefly until it is present.
if (!installBstrHooks()) {
  var attempts = 0;
  var timer = setInterval(function () {
    attempts++;
    if (installBstrHooks() || attempts > 100) clearInterval(timer);
  }, 100);
}

// Watch thread creation so we can see the worker being spawned.
try {
  var beginthreadex = Process.findModuleByName('MSVCR110.dll').findExportByName('_beginthreadex');
  Interceptor.attach(beginthreadex, {
    onEnter: function (args) {
      var start = args[2];
      var mod = Process.findModuleByAddress(start);
      emit('thread-start', {
        entry: mod ? (mod.name + '+0x' + start.sub(mod.base).toString(16)) : String(start)
      });
    }
  });
  emit('hooked', { api: 'MSVCR110!_beginthreadex' });
} catch (e) {
  emit('hook-failed', { target: '_beginthreadex', error: String(e) });
}

Process.setExceptionHandler(function (details) {
  if (details.type !== 'access-violation') return false;
  emit('FAULT', {
    rva: '0x' + details.address.sub(game.base).toString(16),
    rcx: String(details.context.rcx),
    bstrsSeen: bstrCount
  });
  return false;
});

emit('ready', { base: game.base.toString() });
