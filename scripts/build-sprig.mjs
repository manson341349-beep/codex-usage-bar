// Reproducible offline runtime bundle; npm ci is only needed for development.
import {build} from 'esbuild';
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {dirname,resolve} from 'node:path';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const result=await build({absWorkingDir:root,entryPoints:['web/sprig-source/runtime.js'],bundle:true,format:'iife',target:['chrome120'],minify:true,legalComments:'inline',write:false,banner:{js:'/* codex-usage-bar Sprig — MIT. Includes Three.js r180, MIT: web/THREE-LICENSE.txt. */'}});
const output=result.outputFiles[0].contents,path=resolve(root,'web/sprig.js');
if(process.argv.includes('--check')){const old=await readFile(path);if(!old.equals(Buffer.from(output)))throw new Error('Sprig bundle differs from its checked-in source');console.log('Sprig bundle is reproducible ('+output.length+' bytes)')}
else{await writeFile(path,output);console.log('Built Sprig ('+output.length+' bytes)')}
