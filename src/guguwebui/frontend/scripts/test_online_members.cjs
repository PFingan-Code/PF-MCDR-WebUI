/* eslint-disable no-console */
/**
 * onlineMembers.ts 单元测试。
 *
 * 与 test_format.cjs 同一套零依赖做法：用 devDependency 自带的 typescript
 * 把 TS 转译为 CommonJS 后执行断言。
 * 运行：pnpm test:online-members（已被 pnpm test:format 串联，CI 覆盖）
 */
const path = require('path')
const ts = require('typescript')

const root = path.resolve(__dirname, '..')
const srcPath = path.join(root, 'src', 'utils', 'onlineMembers.ts')
const source = require('fs').readFileSync(srcPath, 'utf8')

const out = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
  fileName: srcPath,
}).outputText

const moduleExports = {}
new Function('module', 'exports', 'require', out)(moduleExports, moduleExports, require)
const m = moduleExports

let failures = 0
function assert(condition, label) {
  if (condition) {
    console.log(`  ✓ ${label}`)
  } else {
    failures += 1
    console.error(`  ✗ ${label}`)
  }
}

console.log('onlineMembers.ts 单测')

// 同一个玩家同时在游戏内与网页在线 → 必须合并为一条
const merged = m.mergeOnlineMembers({ web: ['Shusao'], game: ['Shusao'], bot: [] })
assert(merged.length === 1, '同名玩家(游戏内+网页)合并为 1 条')
assert(merged[0].name === 'Shusao', '保留玩家名')
assert(
  JSON.stringify(merged[0].statuses) === JSON.stringify(['game', 'web']),
  '来源合并为 [game, web]',
)

// 顺序：game → bot → web，且只在 web 的玩家仍单独出现
const ordered = m.mergeOnlineMembers({ web: ['WebOnly', 'Shusao'], game: ['Shusao'], bot: ['FakeBot'] })
assert(
  JSON.stringify(ordered.map(item => item.name)) === JSON.stringify(['Shusao', 'FakeBot', 'WebOnly']),
  '顺序为 游戏内 → 假人 → 仅网页',
)
assert(
  JSON.stringify(ordered.find(item => item.name === 'Shusao').statuses) === JSON.stringify(['game', 'web']),
  '合并条目的来源完整',
)

// 公开聊天页不展示 bot
const withoutBots = m.mergeOnlineMembers({ web: ['Shusao'], game: [], bot: ['FakeBot'] }, { includeBots: false })
assert(
  JSON.stringify(withoutBots.map(item => item.name)) === JSON.stringify(['Shusao']),
  'includeBots=false 时不含假人',
)

// 去重计数：同名玩家只算一次
assert(m.countOnlinePlayers({ web: ['Shusao'], game: ['Shusao'], bot: [] }) === 1, '去重后在线玩家数为 1')
assert(m.countOnlinePlayers({ web: ['B'], game: ['A'], bot: ['FakeBot'] }) === 2, 'bot 不计入玩家数')

// 边界：空 / undefined / 空字符串 / 空白名字
assert(m.mergeOnlineMembers(undefined).length === 0, 'undefined 输入 → 空数组')
assert(m.mergeOnlineMembers({ web: [], game: [], bot: [] }).length === 0, '全空 → 空数组')
assert(m.mergeOnlineMembers({ web: ['', '  '], game: [] }).length === 0, '空白名字被忽略')

// 重复项来自同一数组时也只保留一条
const duplicated = m.mergeOnlineMembers({ game: ['A', 'A'], web: [] })
assert(duplicated.length === 1, '同一来源重复名字只保留 1 条')
assert(
  JSON.stringify(duplicated[0].statuses) === JSON.stringify(['game']),
  '同一来源重复名字不会重复记录来源',
)

if (failures) {
  console.error(`\n${failures} 个断言失败`)
  process.exit(1)
}
console.log('\n全部通过')
