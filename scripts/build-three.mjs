import {build} from 'esbuild';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
const pkg=JSON.parse(readFileSync('package.json','utf8'));
const dir=`public/vendor/three-${pkg.devDependencies.three}`;
const result=await build({stdin:{contents:"export * from 'three';",resolveDir:process.cwd()},bundle:true,minify:true,format:'esm',target:'es2020',write:false,legalComments:'inline'});
const output=`${dir}/three.module.js`;
if(process.argv.includes('--check')) {
 if(!readFileSync(output).equals(result.outputFiles[0].contents))throw Error('Three.js bundle differs; run npm run vendor:three');
} else {
 mkdirSync(dir,{recursive:true});writeFileSync(output,result.outputFiles[0].contents);
 writeFileSync(`${dir}/LICENSE.txt`,readFileSync('node_modules/three/LICENSE'));
}
console.log('Verified Three.js '+pkg.devDependencies.three);
