/**
 * 在线成员合并。
 *
 * 后端把在线状态按来源分成 web / game / bot 三个数组：同一个玩家可能既在游戏内、
 * 又通过网页聊天页保持心跳在线，分别渲染就会出现两条同名记录。这里按名字合并为
 * 一条，并保留该成员的全部在线来源，供界面显示 “GAME · WEB”。
 */
import type { ChatOnlineStatus } from '../types/api'

export type OnlineMemberStatus = 'game' | 'web' | 'bot'

export interface OnlineMember {
  name: string
  /** 该成员当前在线的全部来源，顺序：game → bot → web */
  statuses: OnlineMemberStatus[]
}

export interface MergeOnlineMembersOptions {
  /** 是否包含假人(bot)；公开聊天页不展示 bot */
  includeBots?: boolean
}

export function mergeOnlineMembers(
  online?: Partial<ChatOnlineStatus> | null,
  options: MergeOnlineMembersOptions = {},
): OnlineMember[] {
  const { includeBots = true } = options
  const members = new Map<string, OnlineMember>()

  const add = (names: string[] | undefined, status: OnlineMemberStatus) => {
    for (const raw of names ?? []) {
      const name = (raw ?? '').trim()
      if (!name) continue
      const existing = members.get(name)
      if (existing) {
        if (!existing.statuses.includes(status)) existing.statuses.push(status)
      } else {
        members.set(name, { name, statuses: [status] })
      }
    }
  }

  add(online?.game, 'game')
  if (includeBots) add(online?.bot, 'bot')
  add(online?.web, 'web')

  return Array.from(members.values())
}

/** 合并后的去重在线玩家数（不含仅 bot 的条目） */
export function countOnlinePlayers(online?: Partial<ChatOnlineStatus> | null): number {
  return mergeOnlineMembers(online, { includeBots: false }).length
}
