/*
 * Find the code path that resolves the Blaze redirector and then gives up.
 *
 * Established: FIFA 15 resolves spring14.gosredirector.ea.com successfully
 * (DnsQueryEx returns 0 with A 159.153.51.19) yet never opens a TCP connection
 * to anything — confirmed by both frida hooks and OS-level polling. Resolving
 * the redirector is step one of the Blaze connect sequence, so the client is
 * inside its connection code and abandoning it.
 *
 * This captures a backtrace at the DNS call to identify the caller inside
 * fifa15.exe, then watches that caller: how long it runs, and what it returns.
 * That points at the decision, instead of guessing from symptoms.
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

var game = Process.findModuleByName('fifa15.exe');

function describe(address) {
  var mod = Process.findModuleByAddress(address);
  return mod ? (mod.name + '+0x' + address.sub(mod.base).toString(16)) : String(address);
}

function backtrace(context) {
  var frames = [];
  try {
    frames = Thread.backtrace(context, Backtracer.ACCURATE).map(describe);
  } catch (e) {}
  if (frames.length < 3) {
    try {
      frames = Thread.backtrace(context, Backtracer.FUZZY).map(describe);
    } catch (e) {}
  }
  return frames.slice(0, 22);
}

var seenStacks = {};

// --- name resolution: who asks, and from where -----------------------------
var dnsQueryEx = resolveExport('dnsapi.dll', 'DnsQueryEx');
if (dnsQueryEx) {
  Interceptor.attach(dnsQueryEx, {
    onEnter: function (args) {
      var name = null;
      try {
        var p = args[0].isNull() ? null : args[0].add(8).readPointer();
        if (p && !p.isNull()) name = p.readUtf16String();
      } catch (e) {}
      if (!name || name.indexOf('gosredirector') < 0) return;

      var frames = backtrace(this.context);
      var key = frames.slice(0, 6).join('|');
      if (seenStacks[key]) return;
      seenStacks[key] = true;
      emit('dns-caller', { host: name, stack: frames, thread: Process.getCurrentThreadId() });
    }
  });
  emit('hooked', { api: 'DnsQueryEx (with caller stack)' });
}

// --- socket creation: who creates the TCP socket that never connects -------
var socketFn = resolveExport('ws2_32.dll', 'socket');
if (socketFn) {
  Interceptor.attach(socketFn, {
    onEnter: function (args) { this.type = args[1].toInt32(); },
    onLeave: function (retval) {
      if (this.type !== 1) return; // SOCK_STREAM only
      var frames = backtrace(this.context);
      var key = 'sock' + frames.slice(0, 6).join('|');
      if (seenStacks[key]) return;
      seenStacks[key] = true;
      emit('tcp-socket-caller', {
        handle: retval.toInt32(), stack: frames, thread: Process.getCurrentThreadId()
      });
    }
  });
  emit('hooked', { api: 'ws2_32!socket (TCP, with caller stack)' });
}

// --- closesocket: a socket created then closed without connecting is the tell
var closeSocket = resolveExport('ws2_32.dll', 'closesocket');
if (closeSocket) {
  Interceptor.attach(closeSocket, {
    onEnter: function (args) {
      var frames = backtrace(this.context);
      var key = 'close' + frames.slice(0, 5).join('|');
      if (seenStacks[key]) return;
      seenStacks[key] = true;
      emit('closesocket-caller', { handle: args[0].toInt32(), stack: frames });
    }
  });
  emit('hooked', { api: 'ws2_32!closesocket (with caller stack)' });
}

// --- setsockopt/bind often precede a connect; useful ordering evidence -----
['bind', 'setsockopt', 'ioctlsocket'].forEach(function (name) {
  var address = resolveExport('ws2_32.dll', name);
  if (!address) return;
  var count = 0;
  Interceptor.attach(address, {
    onEnter: function () {
      if (++count > 6) return;
      emit('socket-setup', { api: name, n: count });
    }
  });
});

emit('dns-caller-tracer-ready', {});
