import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compile} from '@vue/compiler-dom';
import AtscDx from '../atsc-dx.js';

test('ATSC diagnostics, scan and reception log template compiles',()=>{
  const errors=[];
  compile(AtscDx.template,{onError:e=>errors.push(e.message)});
  assert.deepEqual(errors,[]);
});
