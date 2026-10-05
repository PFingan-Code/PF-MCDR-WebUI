import { AnimatePresence, motion } from 'framer-motion'
import {
  ChevronLeft,
  Loader2,
  MessageSquare,
  RefreshCw,
  Send,
  Users,
  UserX
} from 'lucide-react'
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { MessageLineSkeleton } from '../components/Skeleton'
import { useAuth } from '../hooks/useAuth'
import api, { unwrapData } from '../utils/api'
import { mergeOnlineMembers, type OnlineMemberStatus } from '../utils/onlineMembers'
import { parseRText } from '../utils/rtextParser'
import type { ChatMessage, ChatOnlineStatus } from '../types/api'

interface ServerStatus {
  status: string
  version: string
  players: string
}

type OnlineStatus = ChatOnlineStatus

const Chat: React.FC = () => {
  const { t } = useTranslation()
  const { username } = useAuth()

  // Chat state
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const chatMessagesRef = useRef<ChatMessage[]>([])
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  const [initialMessagesLoaded, setInitialMessagesLoaded] = useState(false)
  const [hasMoreMessages, setHasMoreMessages] = useState(true)
  const [isInitialScrollPending, setIsInitialScrollPending] = useState(false)
  const [chatMessage, setChatMessage] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [lastSendAtMs, setLastSendAtMs] = useState(0)
  const [sendError, setSendError] = useState('')

  // Server state
  const [serverStatus, setServerStatus] = useState<ServerStatus>({ status: 'unknown', version: '', players: '0/0' })
  const [onlineStatus, setOnlineStatus] = useState<OnlineStatus>({ web: [], game: [], bot: [] })
  const [showOnlinePanel, setShowOnlinePanel] = useState(false)

  const chatContainerRef = useRef<HTMLDivElement>(null)
  const statusFetchingRef = useRef(false)
  const newMessagesFetchingRef = useRef(false)
  const shouldScrollToNewMessagesRef = useRef(false)

  // Sync ref with state
  useEffect(() => {
    chatMessagesRef.current = chatMessages
  }, [chatMessages])

  // Initialize
  useEffect(() => {
    fetchInitialMessages()
  }, [])

  // Auto scroll logic
  const scrollToBottom = (behavior: ScrollBehavior = 'smooth') => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTo({
        top: chatContainerRef.current.scrollHeight,
        behavior
      })
    }
  }

  // Scroll after the initial batch is committed to the DOM; keep polling anchored only when already at bottom.
  useEffect(() => {
    if (!chatContainerRef.current || isLoadingMessages) return
    const container = chatContainerRef.current
    const isAtBottom = container.scrollHeight - container.scrollTop <= container.clientHeight + 100
    if (isInitialScrollPending) {
      container.scrollTop = container.scrollHeight
      setIsInitialScrollPending(false)
    } else if (shouldScrollToNewMessagesRef.current && isAtBottom && chatMessages.length > 0) {
      shouldScrollToNewMessagesRef.current = false
      scrollToBottom('smooth')
    }
  }, [chatMessages, isLoadingMessages, isInitialScrollPending])

  const fetchInitialMessages = useCallback(async () => {
    setIsLoadingMessages(true)
    try {
      const resp = await api.get('/chat/messages', { params: { limit: 50, offset: 0 } })
      const d = unwrapData<{ items?: ChatMessage[] }>(resp)
      const msgs = d?.items || []
      // API returns newest first; the view is always oldest -> newest.
      setChatMessages([...msgs].sort((a, b) => a.id - b.id))
      setHasMoreMessages(msgs.length > 0 && Math.min(...msgs.map((m: ChatMessage) => m.id)) > 1)
      setIsInitialScrollPending(true)
    } catch (e) {
      console.error('Failed to load messages', e)
    } finally {
      setIsLoadingMessages(false)
      setInitialMessagesLoaded(true)
    }
  }, [])

  const loadNewMessages = useCallback(async () => {
    if (newMessagesFetchingRef.current) return
    newMessagesFetchingRef.current = true

    const currentMaxId = chatMessagesRef.current.length > 0 ? Math.max(...chatMessagesRef.current.map(m => m.id)) : 0
    const container = chatContainerRef.current
    shouldScrollToNewMessagesRef.current = !!container &&
      container.scrollHeight - container.scrollTop <= container.clientHeight + 100

    try {
      const resp = await api.get('/chat/messages/incremental', {
        params: { after_id: currentMaxId, player_id: username }
      })
      const d = unwrapData<{ messages?: ChatMessage[]; online?: OnlineStatus }>(resp)
      if (d?.messages && d.messages.length > 0) {
        const newMsgs = [...d.messages].sort((a, b) => a.id - b.id)
        setChatMessages(prev => {
          const byId = new Map(prev.map(msg => [msg.id, msg]))
          newMsgs.forEach(msg => byId.set(msg.id, msg))
          return Array.from(byId.values()).sort((a, b) => a.id - b.id)
        })
      }
      if (d?.online) {
        setOnlineStatus({
          web: d.online.web || [],
          game: d.online.game || [],
          bot: d.online.bot || []
        })
      }
    } catch (e) {
      console.error('Failed to load new messages', e)
    } finally {
      newMessagesFetchingRef.current = false
    }
  }, [username])

  const fetchServerStatus = useCallback(async () => {
    if (statusFetchingRef.current) return
    statusFetchingRef.current = true
    try {
      const resp = await api.get('/server/status')
      const st = unwrapData<{ online?: boolean; version?: string; players?: string }>(resp)
      setServerStatus({
        status: st?.online ? 'online' : 'unknown',
        version: st?.version || '',
        players: st?.players || '0/0'
      })
    } catch (e) {
      // ignore status polling error
    }
    finally {
      statusFetchingRef.current = false
    }
  }, [])

  // Poll for status (always) and for new messages (only after initial load)
  useEffect(() => {
    fetchServerStatus()
    const statusTimer = setInterval(fetchServerStatus, 5000)
    return () => clearInterval(statusTimer)
  }, [fetchServerStatus])

  useEffect(() => {
    if (!initialMessagesLoaded) return
    const messageTimer = setInterval(loadNewMessages, 2000)
    return () => clearInterval(messageTimer)
  }, [initialMessagesLoaded, loadNewMessages])

  const loadChatMessages = async (limit = 50, beforeId = 0) => {
    if (isLoadingMessages) return
    setIsLoadingMessages(true)

    // Save current scroll height to maintain position
    const scrollHeight = chatContainerRef.current?.scrollHeight || 0

    try {
      const resp = await api.get('/chat/messages', { params: { limit, before_id: beforeId } })
      const d = unwrapData<{ items?: ChatMessage[] }>(resp)
      const msgs = d?.items || []
      // History is returned newest -> oldest; prepend the sorted batch and deduplicate by ID.
      const historicalMsgs = [...msgs].sort((a, b) => a.id - b.id)
      setChatMessages(prev => {
        const byId = new Map(prev.map(msg => [msg.id, msg]))
        historicalMsgs.forEach(msg => byId.set(msg.id, msg))
        return Array.from(byId.values()).sort((a, b) => a.id - b.id)
      })
      setHasMoreMessages(msgs.length > 0 && Math.min(...msgs.map((m: ChatMessage) => m.id)) > 1)

      // After DOM update, restore scroll position
      requestAnimationFrame(() => {
        if (chatContainerRef.current) {
          const newScrollHeight = chatContainerRef.current.scrollHeight
          chatContainerRef.current.scrollTop = newScrollHeight - scrollHeight
        }
      })
    } catch (e) {
      console.error('Failed to load historical messages', e)
    } finally {
      setIsLoadingMessages(false)
    }
  }

  const notifySendError = (message: string) => {
    setSendError(message)
    setTimeout(() => setSendError(''), 4000)
  }

  const handleSendMessage = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!chatMessage.trim() || isSending) return

    const now = Date.now()
    if (now - lastSendAtMs < 2000) return

    setIsSending(true)
    try {
      const resp = await api.post('/chat/messages', {
        message: chatMessage.trim(),
        player_id: username
      })

      if (resp.data.status === 'success') {
        setChatMessage('')
        setLastSendAtMs(Date.now())
        loadNewMessages()
      } else {
        notifySendError(resp.data.message || t('page.chat.msg.send_failed'))
      }
    } catch (e) {
      // 后端拒绝（会话失效 / 聊天到游戏未启用等）时必须提示，避免“发送后无反应”
      const err = e as { response?: { data?: { message?: string } } }
      notifySendError(err?.response?.data?.message || t('page.chat.msg.network_send_failed'))
    } finally {
      setIsSending(false)
    }
  }

  const handleKickPlayer = async (name: string) => {
    if (!window.confirm(t('page.chat.kick_confirm', { name }))) return
    try {
      await api.post('/server/commands', { command: `/kick ${name}` })
      loadNewMessages()
    } catch (e) {
      console.error('Kick failed', e)
    }
  }

  const renderMessageContent = (msg: ChatMessage): React.ReactNode => {
    if (msg.message_source === 'webui') return msg.message
    if (msg.is_rtext && msg.rtext_data) {
      return parseRText(msg.rtext_data, {
        onCommandClick: async (command: string) => {
          try {
            await api.post('/chat/messages', {
              message: command,
              player_id: username
            })
            loadNewMessages()
          } catch (e) {
            console.error('执行命令失败:', e)
          }
        },
        onCommandSuggest: (command: string) => {
          setChatMessage(command)
        }
      })
    }
    return msg.message
  }

  const formatMessageDateTime = (ts: number) => {
    const d = new Date(ts * 1000)
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    return time
  }

  // 同一玩家可能同时游戏内 + 网页在线：合并为一条，避免同名重复显示。
  // 仍按来源分组，但已归入“游戏内/假人”的玩家不会再出现在“网页”分组里。
  const onlineMembers = mergeOnlineMembers(onlineStatus)
  const gameMembers = onlineMembers.filter(member => member.statuses.includes('game'))
  const botMembers = onlineMembers.filter(
    member => member.statuses.includes('bot') && !member.statuses.includes('game')
  )
  const webMembers = onlineMembers.filter(
    member => member.statuses.includes('web') && !member.statuses.includes('game') && !member.statuses.includes('bot')
  )

  return (
    <div className="h-[calc(100vh-8rem)] flex flex-col gap-4 animate-in fade-in slide-in-from-bottom-4 duration-500">

      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-white dark:bg-slate-900 p-3 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-sm shrink-0">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-blue-600 text-white shadow-lg shadow-blue-500/20">
            <MessageSquare size={20} />
          </div>
          <div>
            <h2 className="font-bold text-slate-900 dark:text-white flex items-center gap-2">
              {t('page.chat.header_title')}
              {serverStatus.version && <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 font-mono">{serverStatus.version}</span>}
            </h2>
            <div className="flex items-center gap-2 text-xs text-slate-500 font-bold uppercase tracking-wider">
              <span className={`w-2 h-2 rounded-full ${serverStatus.status === 'running' || serverStatus.status === 'online' ? 'bg-emerald-500' : 'bg-slate-400'}`} />
              {serverStatus.players || '0/0'} {t('page.chat.header.players_online')}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowOnlinePanel(!showOnlinePanel)}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-all ${showOnlinePanel ? 'bg-blue-50 dark:bg-blue-900/30 border-blue-200 dark:border-blue-800 text-blue-600 dark:text-blue-400' : 'bg-white dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-slate-400'}`}
          >
            <Users size={18} />
            <span className="hidden sm:inline text-xs font-bold uppercase tracking-wider">{t('page.chat.panel.online_members')}</span>
          </button>
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden gap-4">
        {/* Chat Area */}
        <div className="flex-1 flex flex-col min-w-0 bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
          <div
            ref={chatContainerRef}
            className="flex-1 overflow-y-auto p-4 md:p-6 custom-scrollbar bg-slate-950"
          >
            {hasMoreMessages && (
              <button
                onClick={() => chatMessages.length > 0 && loadChatMessages(50, Math.min(...chatMessages.map(m => m.id)))}
                disabled={isLoadingMessages}
                className="w-full py-2 text-xs font-bold text-slate-500 hover:text-blue-500 transition-colors flex items-center justify-center gap-2 mb-4"
              >
                {isLoadingMessages ? <Loader2 className="animate-spin w-4 h-4" /> : <RefreshCw className="w-4 h-4" />}
                {t('page.chat.room.load_more')}
              </button>
            )}

            <div className="space-y-0.5 font-mono text-sm">
              {isLoadingMessages && chatMessages.length === 0 ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <MessageLineSkeleton key={i} variant="terminal" />
                ))
              ) : (
                chatMessages.map((msg, i) => (
                  <div key={`${msg.id}-${i}`} className="flex items-baseline gap-2 py-0.5 group">
                    <span className="text-slate-500 shrink-0 text-xs">[{formatMessageDateTime(msg.timestamp)}]</span>
                    <span className={`font-bold shrink-0 ${msg.player_id === username ? 'text-blue-400' : 'text-amber-500'}`}>
                      {msg.player_id}:
                    </span>
                    <span className="text-slate-200 break-words leading-relaxed">{renderMessageContent(msg)}</span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Input Area */}
          <div className="p-4 bg-white dark:bg-slate-900 border-t border-slate-100 dark:border-slate-800">
            {sendError && (
              <p className="mb-2 text-xs text-red-500">{sendError}</p>
            )}
            <form onSubmit={handleSendMessage} className="flex gap-2">
              <input
                type="text"
                value={chatMessage}
                onChange={(e) => setChatMessage(e.target.value)}
                placeholder={t('page.chat.room.input_placeholder_send')}
                className="flex-1 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-2.5 
                  focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none text-sm transition-all"
              />
              <button
                type="submit"
                disabled={!chatMessage.trim() || isSending}
                className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-6 rounded-xl 
                  shadow-lg shadow-blue-500/30 transition-all flex items-center justify-center"
              >
                {isSending ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} className="sm:mr-2" />}
                <span className="hidden sm:inline font-bold uppercase tracking-widest text-xs">{t('page.chat.room.send.submit')}</span>
              </button>
            </form>
          </div>
        </div>

        {/* Online List Panel */}
        <AnimatePresence>
          {showOnlinePanel && (
            <motion.div
              initial={{ x: 20, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: 20, opacity: 0 }}
              className="w-72 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 
                rounded-2xl shadow-sm flex flex-col shrink-0 overflow-hidden"
            >
              <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-slate-50/50 dark:bg-slate-800/50">
                <h4 className="font-bold text-sm uppercase tracking-wider text-slate-500">{t('page.chat.panel.online_members')}</h4>
                <button onClick={() => setShowOnlinePanel(false)} className="text-slate-400 hover:text-slate-600"><ChevronLeft size={18} /></button>
              </div>
              <div className="flex-1 overflow-y-auto p-2 space-y-1 custom-scrollbar">
                {/* Game Players */}
                {gameMembers.length > 0 && (
                  <div className="mb-4">
                    <p className="px-2 mb-1 text-[10px] font-black text-slate-400 uppercase tracking-widest">{t('page.chat.offline.status_game')}</p>
                    {gameMembers.map(member => (
                      <PlayerItem key={member.name} name={member.name} statuses={member.statuses} onKick={() => handleKickPlayer(member.name)} />
                    ))}
                  </div>
                )}
                {/* Bots */}
                {botMembers.length > 0 && (
                  <div className="mb-4">
                    <p className="px-2 mb-1 text-[10px] font-black text-slate-400 uppercase tracking-widest">BOTS</p>
                    {botMembers.map(member => (
                      <PlayerItem key={member.name} name={member.name} statuses={member.statuses} onKick={() => handleKickPlayer(member.name)} />
                    ))}
                  </div>
                )}
                {/* Web Players */}
                {webMembers.length > 0 && (
                  <div className="mb-4">
                    <p className="px-2 mb-1 text-[10px] font-black text-slate-400 uppercase tracking-widest">{t('page.chat.offline.status_web')}</p>
                    {webMembers.map(member => (
                      <PlayerItem key={member.name} name={member.name} statuses={member.statuses} />
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

const MEMBER_DOT_CLASS: Record<OnlineMemberStatus, string> = {
  game: 'bg-emerald-500',
  web: 'bg-blue-500',
  bot: 'bg-purple-500',
}

const PlayerItem: React.FC<{ name: string; statuses: OnlineMemberStatus[]; onKick?: () => void }> = ({ name, statuses, onKick }) => {
  return (
    <div className="flex items-center justify-between p-2 rounded-xl hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors group">
      <div className="flex items-center gap-2 min-w-0">
        {/* 同一玩家可能同时来自多个来源（游戏内 + 网页），逐个显示状态点 */}
        <span className="flex items-center gap-0.5 shrink-0">
          {statuses.map(status => (
            <span key={status} className={`w-2 h-2 rounded-full ${MEMBER_DOT_CLASS[status]}`} />
          ))}
        </span>
        <span className="text-sm font-medium text-slate-700 dark:text-slate-200 truncate">{name}</span>
      </div>
      {onKick && (
        <button
          onClick={(e) => { e.stopPropagation(); onKick(); }}
          className="p-1.5 text-slate-400 hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-lg transition-all opacity-0 group-hover:opacity-100"
          title="Kick"
        >
          <UserX size={14} />
        </button>
      )}
    </div>
  )
}

export default Chat