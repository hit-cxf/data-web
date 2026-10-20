'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const Otel=require('../public/otel.js');

test('SDK resource metadata distinguishes SDK language and SDK version from service version',()=>{
 const sdk=Otel.sdk({'telemetry.sdk.language':'nodejs','telemetry.sdk.version':'2.0.1','service.version':'9.9.9'});
 assert.equal(sdk.languageLabel,'Node.js');assert.equal(sdk.version,'2.0.1');assert.equal(Otel.sdk({'service.version':'9.9.9'}),null);
});
test('numeric SpanKind needs an explicit SDK or OTLP encoding',()=>{
 assert.match(Otel.spanKind(1,'js'),/^SERVER/);assert.match(Otel.spanKind(1,'otlp'),/^INTERNAL/);
 assert.match(Otel.spanKind(1),/待确认编码/);assert.match(Otel.spanKind('SPAN_KIND_CLIENT'),/^CLIENT/);
});
test('unset, missing and unknown status are not success',()=>{
 assert.equal(Otel.status(0).kind,'unset');assert.equal(Otel.status(undefined).kind,'unknown');
 assert.equal(Otel.status('something').kind,'unknown');assert.equal(Otel.status(1).kind,'ok');
 assert.equal(Otel.status('STATUS_CODE_ERROR').kind,'error');assert.equal(Otel.status({code:2}).kind,'error');
});
test('typed OTel attributes retain duplicate keys, large integer strings and prototype-like keys',()=>{
 const resource=[{key:'telemetry.sdk.language',value:{stringValue:'nodejs'}},{key:'count',value:{intValue:'9007199254740993'}},{key:'x',value:{intValue:'1'}},{key:'x',value:{intValue:'2'}},{key:'__proto__',value:{stringValue:'literal'}}];
 const attrs=Otel.attributes(resource);assert.equal(attrs.count,'9007199254740993');assert.deepEqual(attrs.x,['1','2']);assert.equal(attrs.__proto__,'literal');assert.equal(Otel.sdk(resource).languageLabel,'Node.js');
});
test('resource and Span grouping never drops unknown or complex attributes',()=>{
 const input={'host.name':'a','telemetry.sdk.version':'x',custom:{deep:[1,false,null]}};
 const output=Object.fromEntries(Otel.groups(input,true).flatMap(g=>g.entries));assert.deepEqual(output,input);
 const events={'exception.stacktrace':'line1\nline2','input':{text:'complete'},'custom.key':0};
 assert.deepEqual(Object.fromEntries(Otel.groups(events).flatMap(g=>g.entries)),events);
});
test('SDK high resolution time pairs validate nanoseconds',()=>{
 assert.equal(Otel.hrtime([1700000000,500000000]),1700000000.5);
 assert.equal(Otel.hrtime([1,1e9]),null);assert.equal(Otel.hrtime([true,0]),null);assert.equal(Otel.hrtime([1]),null);
});
