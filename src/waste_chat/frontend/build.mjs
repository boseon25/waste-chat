import { build } from 'esbuild';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = dirname(fileURLToPath(import.meta.url));
const outdir = join(frontend, '../web/ui');

const result = await build({
  absWorkingDir: frontend,
  entryPoints: { app: 'src/main.jsx', finish: 'src/finish.jsx' },
  outdir,
  bundle: true,
  minify: true,
  format: 'iife',
  target: ['es2022'],
  charset: 'utf8',
  jsx: 'automatic',
  define: { 'process.env.NODE_ENV': '"production"' },
  legalComments: 'eof',
  metafile: true,
  write: false,
});

// Collect notices only for packages that contribute code or CSS to the bundles.
const packages = new Set();
for (const output of Object.values(result.metafile.outputs)) {
  for (const [input, details] of Object.entries(output.inputs)) {
    if (!details.bytesInOutput) continue;
    const match = input.match(/^node_modules\/((?:@[^/]+\/)?[^/]+)/);
    if (match) packages.add(match[1]);
  }
}
const upstreamLicenses = {
  '@astryxdesign/core': 'astryx-LICENSE.txt',
  '@stylexjs/stylex': 'stylex-LICENSE.txt',
};
const notices = [];
for (const name of [...packages].sort()) {
  const dir = join(frontend, 'node_modules', name);
  const metadata = JSON.parse(await readFile(join(dir, 'package.json'), 'utf8'));
  const candidates = (await readdir(dir)).filter(f => /^(licen[cs]e|copying|notice)(\.|$)/i.test(f));
  const files = candidates.map(f => join(dir, f));
  if (!files.length && upstreamLicenses[name]) files.push(join(frontend, 'licenses', upstreamLicenses[name]));
  if (!files.length) throw new Error('Missing license text for ' + name);
  const licenses = await Promise.all(files.map(f => readFile(f, 'utf8')));
  notices.push([name + '@' + metadata.version, ...licenses.map(s => s.trimEnd())].join('\n\n'));
}
await mkdir(outdir, { recursive: true });
await Promise.all(result.outputFiles.map(f => writeFile(f.path, f.contents)));
await writeFile(join(outdir, 'THIRD_PARTY_NOTICES.txt'), notices.join('\n\n' + '='.repeat(72) + '\n\n') + '\n');
console.log(result.outputFiles.map(f => f.path.slice(f.path.lastIndexOf('/') + 1)).join(', ') + ' → web/ui/');
