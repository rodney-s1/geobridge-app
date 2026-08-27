#!/usr/bin/env node
/**
 * release.js — one-command release: bump version, build, and publish a
 * GitHub Release with the installer attached (so "Check for Updates" in
 * the app can actually find something).
 *
 * MUST be run on the machine that builds the installer (Windows, using the
 * project's real .venv) — never in a Linux CI/sandbox, because the packaged
 * app bundles a platform-native Python virtualenv (extraResources: .venv)
 * containing compiled C-extension wheels (cryptography, Pillow, etc.) that
 * are NOT portable across operating systems.
 *
 * Usage (from the project root):
 *   npm run release              # bumps the patch version (1.0.3 -> 1.0.4)
 *   npm run release -- minor     # 1.0.3 -> 1.1.0
 *   npm run release -- major     # 1.0.3 -> 2.0.0
 *   npm run release -- 1.2.3     # set an explicit version
 *
 * What it does, in order:
 *   1. Refuses to run with uncommitted changes (avoid baking half-finished
 *      work into a published release).
 *   2. Refuses to run on any branch other than `main`.
 *   3. `git pull --ff-only origin main` — guarantees the build includes
 *      every commit that's been pushed (this is exactly the step that was
 *      skipped for the mystery "v1.0.4" build that never became a GitHub
 *      Release).
 *   4. `npm version <bump>` — bumps package.json, commits
 *      "vX.Y.Z", and creates an annotated git tag "vX.Y.Z".
 *   5. `npm run build:frontend` — rebuilds frontend/dist.
 *   6. `npx electron-builder --publish always` — packages the Windows
 *      installer AND uploads it as a GitHub Release in one step. Requires
 *      a GH_TOKEN (or GITHUB_TOKEN) environment variable with `repo` scope,
 *      since this is a private repository.
 *   7. `git push origin main --follow-tags` — pushes the version-bump
 *      commit and tag so origin/main and the Release stay in sync.
 *
 * If step 6 fails, the version-bump commit/tag from step 4 already exist
 * locally. Either fix the problem and re-run `npx electron-builder
 * --publish always` directly, or undo the bump with:
 *   git tag -d vX.Y.Z
 *   git reset --hard HEAD~1
 */

const { execSync } = require('child_process')
const path = require('path')
const fs = require('fs')

const ROOT = path.join(__dirname, '..')

function run(cmd, opts = {}) {
  console.log(`\n$ ${cmd}`)
  return execSync(cmd, { cwd: ROOT, stdio: 'inherit', ...opts })
}

function runCapture(cmd) {
  return execSync(cmd, { cwd: ROOT, encoding: 'utf8' }).trim()
}

function fail(message) {
  console.error(`\n[release] ERROR: ${message}\n`)
  process.exit(1)
}

// ── 0. Sanity: bump argument ────────────────────────────────────────────────
const bump = process.argv[2] || 'patch'
const validBumps = ['patch', 'minor', 'major', 'prepatch', 'preminor', 'premajor', 'prerelease']
const isExplicitVersion = /^\d+\.\d+\.\d+/.test(bump)
if (!validBumps.includes(bump) && !isExplicitVersion) {
  fail(`Invalid version argument "${bump}". Use patch | minor | major, or an explicit version like 1.2.3.`)
}

// ── 1. GH_TOKEN present? ────────────────────────────────────────────────────
// electron-builder's GitHub publisher reads GH_TOKEN (or GITHUB_TOKEN).
if (!process.env.GH_TOKEN && !process.env.GITHUB_TOKEN) {
  fail(
    'GH_TOKEN environment variable is not set.\n' +
    '  electron-builder needs a GitHub Personal Access Token with "repo" scope\n' +
    '  to publish a Release to this private repository.\n\n' +
    '  1. Create one at https://github.com/settings/tokens (classic, "repo" scope)\n' +
    '  2. Set it for this PowerShell session before running the release:\n' +
    '       $env:GH_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"\n' +
    '  3. Then re-run:  npm run release'
  )
}

// ── 2. .venv present? (packaged installer bundles it as extraResources) ────
if (!fs.existsSync(path.join(ROOT, '.venv'))) {
  fail(
    '.venv not found in the project root.\n' +
    '  The packaged app bundles this Python virtualenv as extraResources —\n' +
    '  without it here, electron-builder will produce a broken installer.\n' +
    '  Create it first, e.g.:\n' +
    '    python -m venv .venv\n' +
    '    .venv\\Scripts\\pip install -r backend\\requirements.txt'
  )
}

// ── 3. Working tree must be clean ───────────────────────────────────────────
const dirty = runCapture('git status --porcelain')
if (dirty) {
  fail(
    'You have uncommitted changes. Commit or stash them before releasing —\n' +
    'a release should only ever contain code that is already committed:\n\n' +
    dirty
  )
}

// ── 4. Must be on main ───────────────────────────────────────────────────────
const branch = runCapture('git rev-parse --abbrev-ref HEAD')
if (branch !== 'main') {
  fail(`You are on branch "${branch}". Releases must be built from "main". Run: git checkout main`)
}

// ── 5. Pull latest — this is the step that was missed for the "v1.0.4" mixup ─
console.log('\n[release] Pulling latest changes from origin/main...')
try {
  run('git pull --ff-only origin main')
} catch {
  fail(
    'git pull --ff-only failed. Your local main has diverged from origin/main.\n' +
    'Resolve this manually (git status / git log) before releasing — a release\n' +
    'must be built from a main that exactly matches what is on GitHub.'
  )
}

// ── 6. Bump version, commit, tag ────────────────────────────────────────────
console.log(`\n[release] Bumping version (${bump})...`)
let newVersion
try {
  // npm version prints the new version tag (e.g. "v1.0.4") to stdout.
  const out = execSync(`npm version ${bump} -m "chore(release): %s"`, { cwd: ROOT, encoding: 'utf8' })
  newVersion = out.trim().replace(/^v/, '')
  console.log(out)
} catch (e) {
  fail(`npm version failed: ${e.message}`)
}
console.log(`[release] New version: ${newVersion}`)

// ── 7. Build frontend ────────────────────────────────────────────────────────
try {
  run('npm run build:frontend')
} catch {
  fail(
    'Frontend build failed. The version-bump commit/tag were already created locally.\n' +
    'Fix the build error, then either:\n' +
    '  - re-run "npm run build:frontend" and "npx electron-builder --publish always" manually, or\n' +
    `  - undo the bump: git tag -d v${newVersion} && git reset --hard HEAD~1`
  )
}

// ── 8. Package + publish to GitHub Releases ──────────────────────────────────
try {
  run('npx electron-builder --publish always')
} catch {
  fail(
    'electron-builder build/publish failed. The version-bump commit/tag were\n' +
    'already created locally (and the frontend was already rebuilt).\n' +
    'Fix the error, then either:\n' +
    '  - re-run "npx electron-builder --publish always" manually, or\n' +
    `  - undo the bump: git tag -d v${newVersion} && git reset --hard HEAD~1`
  )
}

// ── 9. Push the version-bump commit + tag ────────────────────────────────────
try {
  run('git push origin main --follow-tags')
} catch {
  fail(
    `GitHub Release v${newVersion} was published successfully, but pushing the\n` +
    'version-bump commit/tag to origin failed. Push it manually:\n' +
    '  git push origin main --follow-tags'
  )
}

console.log(
  `\n[release] Done! v${newVersion} is published as a GitHub Release and\n` +
  '           main/tag are pushed. Installed apps will now see it via\n' +
  '           "Check for Updates".\n'
)
