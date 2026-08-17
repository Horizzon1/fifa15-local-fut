/*
 * What answer does FIFA 15 actually get for the EA redirector?
 *
 * Observed so far: the game creates a TCP socket and calls DnsQueryEx for
 * spring14.gosredirector.ea.com, then abandons the attempt — no connect,
 * no WSAConnect, no ConnectEx, and WSAIoctl is never asked for the ConnectEx
 * pointer. So either the DNS answer is failing/being rejected, or the connect
 * goes through something still unhooked.
 *
 * DnsQueryEx is asynchronous: it returns DNS_REQUEST_PENDING (9506) and delivers
 * records through a completion routine. This traces both halves — the request,
 * the immediate return, and the callback's status and records.
 *
 * DNS_QUERY_REQUEST (x64):
 *    0 ULONG  Version
 *    8 PCWSTR QueryName
 *   16 WORD   QueryType
 *   24 ULONG64 QueryOptions
 *   32 PDNS_ADDR_ARRAY pDnsServerList
 *   40 ULONG  InterfaceIndex
 *   48 PDNS_QUERY_COMPLETION_ROUTINE pQueryCompletionCallback
 *   56 PVOID  pQueryContext
 *
 * DNS_QUERY_RESULT (x64):
 *    0 ULONG Version
 *    4 DNS_STATUS QueryStatus
 *    8 ULONG64 QueryOptions
 *   16 PDNS_RECORD pQueryRecords
 *
 * DNS_RECORD (x64):
 *    0 pNext, 8 pName, 16 wType, 18 wDataLength, 20 Flags,
 *   24 dwTtl, 28 dwReserved, 32 Data (A record = 4-byte IPv4)
 */

'use strict';

var DNS_REQUEST_PENDING = 9506;

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

function ipFromDword(pointer) {
  try {
    var b = new Uint8Array(pointer.readByteArray(4));
    return b[0] + '.' + b[1] + '.' + b[2] + '.' + b[3];
  } catch (e) { return null; }
}

function readRecords(head) {
  var records = [];
  var node = head;
  var guard = 0;
  while (node && !node.isNull() && guard++ < 32) {
    try {
      var type = node.add(16).readU16();
      var record = { type: type };
      try { record.name = node.add(8).readPointer().readUtf16String(); } catch (e) {}
      if (type === 1) {          // A
        record.a = ipFromDword(node.add(32));
      } else if (type === 28) {  // AAAA
        record.aaaa = 'ipv6';
      } else if (type === 5) {   // CNAME
        try { record.cname = node.add(32).readPointer().readUtf16String(); } catch (e) {}
      }
      records.push(record);
      node = node.readPointer();
    } catch (e) {
      records.push({ error: String(e) });
      break;
    }
  }
  return records;
}

var callbacksHooked = {};

function hookCompletion(callbackPointer) {
  var key = String(callbackPointer);
  if (callbacksHooked[key]) return;
  callbacksHooked[key] = true;

  // void completion(PVOID pQueryContext, PDNS_QUERY_RESULT pQueryResults)
  Interceptor.attach(callbackPointer, {
    onEnter: function (args) {
      try {
        var results = args[1];
        if (results.isNull()) { emit('dns-callback', { results: 'null' }); return; }
        var status = results.add(4).readU32();
        var recordHead = results.add(16).readPointer();
        emit('dns-callback', {
          status: status,
          statusHex: '0x' + status.toString(16),
          meaning: status === 0 ? 'SUCCESS'
                 : status === 9003 ? 'DNS_ERROR_RCODE_NAME_ERROR (NXDOMAIN)'
                 : status === 9501 ? 'DNS_INFO_NO_RECORDS'
                 : status === 9002 ? 'DNS_ERROR_RCODE_SERVER_FAILURE'
                 : status === 1460 ? 'ERROR_TIMEOUT'
                 : null,
          records: recordHead.isNull() ? [] : readRecords(recordHead)
        });
      } catch (error) {
        emit('dns-callback-error', { error: String(error) });
      }
    }
  });
  emit('hooked', { api: 'DnsQueryEx completion routine', at: key });
}

var address = resolveExport('dnsapi.dll', 'DnsQueryEx');
if (!address) {
  emit('fatal', { error: 'DnsQueryEx not found' });
} else {
  Interceptor.attach(address, {
    onEnter: function (args) {
      this.request = args[0];
      this.results = args[1];
      this.name = null;
      try {
        if (!this.request.isNull()) {
          var namePointer = this.request.add(8).readPointer();
          if (!namePointer.isNull()) this.name = namePointer.readUtf16String();
          this.queryType = this.request.add(16).readU16();
          this.options = this.request.add(24).readU64().toString();
          this.serverList = String(this.request.add(32).readPointer());
          var callback = this.request.add(48).readPointer();
          this.callback = String(callback);
          if (!callback.isNull()) hookCompletion(callback);
        }
      } catch (e) {}
    },
    onLeave: function (retval) {
      var status = retval.toInt32();
      emit('dns-query', {
        host: this.name,
        queryType: this.queryType,
        queryOptions: this.options,
        customDnsServers: this.serverList !== '0x0',
        hasCallback: this.callback !== '0x0',
        returned: status,
        meaning: status === DNS_REQUEST_PENDING ? 'DNS_REQUEST_PENDING (async, callback will deliver)'
               : status === 0 ? 'SUCCESS (synchronous)'
               : 'error ' + status
      });

      // A synchronous success fills pQueryResults directly.
      if (status === 0 && this.results && !this.results.isNull()) {
        try {
          var head = this.results.add(16).readPointer();
          emit('dns-sync-result', {
            status: this.results.add(4).readU32(),
            records: head.isNull() ? [] : readRecords(head)
          });
        } catch (e) {}
      }
    }
  });
  emit('hooked', { api: 'dnsapi!DnsQueryEx (with result tracing)' });
}

emit('dns-result-tracer-ready', {});
