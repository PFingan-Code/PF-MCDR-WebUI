import { AnimatePresence, motion } from 'framer-motion'
import {
  ChevronLeft,
  Loader2,
  Lock,
  LogOut,
  MessageSquare,
  RefreshCw,
  Send,
  Settings,
  ShieldCheck,
  User,
  Users,
  X,
  Zap
} from 'lucide-react'
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import VersionFooter from '../components/VersionFooter'
import { MessageLineSkeleton } from '../components/Skeleton'
import api, { unwrapData } from '../utils/api'
import { countOnlinePlayers, mergeOnlineMembers, type OnlineMemberStatus } from '../utils/onlineMembers'
import { parseRText } from '../utils/rtextParser'
import type { ChatMessage, ChatOnlineStatus } from '../types/api'

interface ServerStatus {
  status: string
  version: string
  players: string
}

type OnlineStatus = ChatOnlineStatus

interface OfflineMember {
  lastSeen: number
  status: 'offline' | 'bot'
  uuid?: string
}

const PlayerChat: React.FC = () => {
  const { t } = useTranslation()
  const [isLoggedIn, setIsLoggedIn] = useState(false)
  const [currentPlayer, setCurrentPlayer] = useState('')
  const [currentPlayerUuid, setCurrentPlayerUuid] = useState('')
  const [currentStep, setCurrentStep] = useState<1 | 2 | 3>(1)
  const [authTab, setAuthTab] = useState<'verify' | 'login'>('login')

  // Auth state
  const [verificationCode, setVerificationCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loginPlayerId, setLoginPlayerId] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [isGenerating, setIsGenerating] = useState(false)
  const [isChecking, setIsChecking] = useState(false)
  const [isSettingPassword, setIsSettingPassword] = useState(false)
  const [isLoggingIn, setIsLoggingIn] = useState(false)
  const [authError, setAuthError] = useState('')
  const [copyFeedback, setCopyFeedback] = useState('')
  // 验证码绑定玩家（check_verification 成功后保存，供 set_password 路径使用）
  const [verifiedPlayerId, setVerifiedPlayerId] = useState('')

  // Chat state
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  // We use a ref for the messages to avoid infinite polling loops and closure issues in intervals
  const chatMessagesRef = useRef<ChatMessage[]>([])
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  const [initialMessagesLoaded, setInitialMessagesLoaded] = useState(false)
  const [hasMoreMessages, setHasMoreMessages] = useState(true)
  const [chatMessage, setChatMessage] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [lastSendAtMs, setLastSendAtMs] = useState(0)

  // Server state
  const [serverStatus, setServerStatus] = useState<ServerStatus>({ status: 'unknown', version: '', players: '0/0' })
  const [onlineStatus, setOnlineStatus] = useState<OnlineStatus>({ web: [], game: [], bot: [] })
  const [offlineMembers, setOfflineMembers] = useState<Record<string, OfflineMember>>({})
  const [showOnlinePanel, setShowOnlinePanel] = useState(false)

  // Preferences
  const [avatarSource, setAvatarSource] = useState<'mccag' | 'mcheads'>('mccag')
  const [chatDisplayMode, setChatDisplayMode] = useState<'modern' | 'mc'>('modern')
  const [showSettings, setShowSettings] = useState(false)

  const chatContainerRef = useRef<HTMLDivElement>(null)
  const statusFetchingRef = useRef(false)
  const newMessagesFetchingRef = useRef(false)

  // Sync ref with state
  useEffect(() => {
    chatMessagesRef.current = chatMessages
  }, [chatMessages])

  // 从聊天记录中提取玩家 uuid，供在线列表头像使用
  const playerUuidMap = React.useMemo(() => {
    const map: Record<string, string> = {}
    chatMessages.forEach(m => {
      if (!m.is_plugin && m.uuid && !map[m.player_id]) map[m.player_id] = m.uuid
    })
    return map
  }, [chatMessages])

  // Initialize
  useEffect(() => {
    const savedAvatar = localStorage.getItem('chat_avatar_source') as 'mccag' | 'mcheads'
    if (savedAvatar) setAvatarSource(savedAvatar)
    const savedDisplayMode = localStorage.getItem('chat_display_mode') as 'modern' | 'mc'
    if (savedDisplayMode) setChatDisplayMode(savedDisplayMode)

    const savedOffline = localStorage.getItem('chat_offline_members')
    if (savedOffline) {
      try {
        const parsed = JSON.parse(savedOffline) as Record<string, OfflineMember>
        const now = Math.floor(Date.now() / 1000)
        const maxOffline = 5 * 60
        const filtered: Record<string, OfflineMember> = {}
        Object.entries(parsed).forEach(([name, info]) => {
          if (now - info.lastSeen <= maxOffline) filtered[name] = info
        })
        setOfflineMembers(filtered)
        if (Object.keys(filtered).length !== Object.keys(parsed).length) {
          localStorage.setItem('chat_offline_members', JSON.stringify(filtered))
        }
      } catch (e) {
        console.warn('Failed to parse offline members', e)
      }
    }

    const checkLogin = async () => {
      const sessionId = localStorage.getItem('chat_session_id')
      if (!sessionId) return

      try {
        const resp = await api.get(`/chat/session/${encodeURIComponent(sessionId)}`)
        const d = unwrapData<{ valid?: boolean; player_id?: string; uuid?: string }>(resp)
        if (d?.valid) {
          setIsLoggedIn(true)
          setCurrentPlayer(d.player_id || '')
          setCurrentPlayerUuid(d.uuid || '')
          fetchInitialMessages()
        } else {
          localStorage.removeItem('chat_session_id')
        }
      } catch (e) {
        localStorage.removeItem('chat_session_id')
      }
    }
    checkLogin()
  }, [])

  // Auto scroll to bottom when new messages arrive
  useEffect(() => {
    if (chatContainerRef.current && !isLoadingMessages) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight
    }
  }, [chatMessages, isLoadingMessages])

  const fetchInitialMessages = useCallback(async () => {
    setIsLoadingMessages(true)
    try {
      const resp = await api.get('/chat/messages', { params: { limit: 50, offset: 0 } })
      const d = unwrapData<{ items?: ChatMessage[] }>(resp)
      const msgs = d?.items || []
      setChatMessages(msgs)
      setHasMoreMessages(msgs.length > 0 && Math.min(...msgs.map((m: ChatMessage) => m.id)) > 1)
    } catch (e) {
      console.error('Failed to load messages', e)
    } finally {
      setIsLoadingMessages(false)
      setInitialMessagesLoaded(true)
    }
  }, [])

  const loadNewMessages = useCallback(async () => {
    if (newMessagesFetchingRef.current || !isLoggedIn) return
    newMessagesFetchingRef.current = true

    const currentMaxId = chatMessagesRef.current.length > 0 ? Math.max(...chatMessagesRef.current.map(m => m.id)) : 0

    try {
      const resp = await api.get('/chat/messages/incremental', {
        params: { after_id: currentMaxId, player_id: currentPlayer }
      })
      const d = unwrapData<{ messages?: ChatMessage[]; online?: OnlineStatus }>(resp)
      if (d?.messages && d.messages.length > 0) {
        setChatMessages(prev => [...d.messages!, ...prev])
      }
      if (d?.online) {
        setOnlineStatus({
          web: d.online.web || [],
          game: d.online.game || [],
          bot: d.online.bot || []
        })
        updateOfflineMembers(d.online)
      }
    } catch (e) {
      console.error('Failed to load new messages', e)
    } finally {
      newMessagesFetchingRef.current = false
    }
  }, [isLoggedIn, currentPlayer])

  const fetchServerStatus = useCallback(async () => {
    if (statusFetchingRef.current) return
    statusFetchingRef.current = true
    try {
      const sessionId = localStorage.getItem('chat_session_id') || ''
      const url = sessionId ? `/server/status?session_id=${encodeURIComponent(sessionId)}` : '/server/status'
      const resp = await api.get(url)
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

  // 登录后立即拉取一次服务器状态，并单独轮询 get_server_status（不依赖 loadNewMessages，避免被 2 秒消息轮询导致 effect 反复清理）
  useEffect(() => {
    if (!isLoggedIn) return
    fetchServerStatus()
    const statusTimer = setInterval(fetchServerStatus, 5000)
    return () => clearInterval(statusTimer)
  }, [isLoggedIn, fetchServerStatus])

  // 消息列表轮询：仅在登录且初始消息加载完成后开始，避免 get_new_messages 在 get_messages 完成前用 after_id: 0 请求
  useEffect(() => {
    if (!isLoggedIn || !initialMessagesLoaded) return
    const messageTimer = setInterval(loadNewMessages, 2000)
    return () => clearInterval(messageTimer)
  }, [isLoggedIn, initialMessagesLoaded, loadNewMessages])

  const notify = (msg: string) => {
    setAuthError(msg)
    setTimeout(() => setAuthError(''), 3000)
  }

  const copyText = async (text: string) => {
    const safeText = text || ''
    if (!safeText) return
    try {
      await navigator.clipboard.writeText(safeText)
      setCopyFeedback(t('page.chat.msg.copy_success'))
      setTimeout(() => setCopyFeedback(''), 1500)
    } catch {
      // Fallback for browsers without clipboard permission
      const textarea = document.createElement('textarea')
      textarea.value = safeText
      textarea.style.position = 'fixed'
      textarea.style.left = '-9999px'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      try {
        document.execCommand('copy')
        setCopyFeedback(t('page.chat.msg.copy_success'))
        setTimeout(() => setCopyFeedback(''), 1500)
      } finally {
        document.body.removeChild(textarea)
      }
    }
  }

  const loadChatMessages = async (limit = 50, beforeId = 0) => {
    if (isLoadingMessages) return
    setIsLoadingMessages(true)
    try {
      const resp = await api.get('/chat/messages', { params: { limit, before_id: beforeId } })
      const d = unwrapData<{ items?: ChatMessage[] }>(resp)
      const msgs = d?.items || []
      setChatMessages(prev => [...prev, ...msgs])
      setHasMoreMessages(msgs.length > 0 && Math.min(...msgs.map((m: ChatMessage) => m.id)) > 1)
    } catch (e) {
      console.error('Failed to load historical messages', e)
    } finally {
      setIsLoadingMessages(false)
    }
  }

  const OFFLINE_HIDE_SECONDS = 5 * 60 // 超过 5 分钟不显示且自动清理

  const updateOfflineMembers = (online: OnlineStatus) => {
    const allOnline = new Set([...online.web, ...online.game, ...online.bot])
    const now = Math.floor(Date.now() / 1000)

    setOfflineMembers(prev => {
      const next = { ...prev }
      Object.keys(next).forEach(name => {
        if (allOnline.has(name)) delete next[name]
        else if (next[name] && (now - next[name].lastSeen) > OFFLINE_HIDE_SECONDS) delete next[name]
      })

      chatMessagesRef.current.forEach(m => {
        if (!m.is_plugin && !allOnline.has(m.player_id) && !next[m.player_id]) {
          next[m.player_id] = {
            lastSeen: m.timestamp,
            status: 'offline',
            uuid: m.uuid
          }
        }
      })

      Object.keys(next).forEach(name => {
        if ((now - next[name].lastSeen) > OFFLINE_HIDE_SECONDS) delete next[name]
      })

      localStorage.setItem('chat_offline_members', JSON.stringify(next))
      return next
    })
  }

  const handleGenerateCode = async () => {
    setIsGenerating(true)
    setAuthError('')
    try {
      const resp = await api.post('/chat/verifications')
      const d = unwrapData<{ code?: string; message?: string }>(resp)
      if (d?.code) {
        setVerificationCode(d.code)
        setCurrentStep(2)
      } else {
        setAuthError(d?.message || t('page.chat.msg.generate_failed'))
      }
    } catch (e) {
      setAuthError(t('page.chat.msg.network_retry'))
    } finally {
      setIsGenerating(false)
    }
  }

  const handleCheckVerification = async () => {
    setIsChecking(true)
    setAuthError('')
    try {
      const resp = await api.get(`/chat/verifications/${encodeURIComponent(verificationCode)}`)
      const d = unwrapData<{ verified?: boolean; player_id?: string; message?: string }>(resp)
      if (d?.verified && d.player_id) {
        setVerifiedPlayerId(d.player_id)
        setCurrentStep(3)
      } else {
        setAuthError(d?.message || t('page.chat.msg.verify_failed'))
      }
    } catch (e) {
      setAuthError(t('page.chat.msg.network_retry'))
    } finally {
      setIsChecking(false)
    }
  }

  const handleSetPassword = async () => {
    if (newPassword !== confirmPassword) {
      setAuthError(t('page.chat.msg.password_mismatch'))
      return
    }
    if (newPassword.length < 6) {
      setAuthError(t('page.chat.msg.password_too_short'))
      return
    }

    setIsSettingPassword(true)
    setAuthError('')
    try {
      const resp = await api.put(`/chat/accounts/${encodeURIComponent(verifiedPlayerId)}/password`, {
        code: verificationCode,
        password: newPassword
      })
      const d = unwrapData<{ player_id?: string; uuid?: string; session_id?: string }>(resp)
      if (resp.data.status === 'success' && d?.session_id) {
        setIsLoggedIn(true)
        setCurrentPlayer(d.player_id || verifiedPlayerId)
        setCurrentPlayerUuid(d.uuid || '')
        localStorage.setItem('chat_session_id', d.session_id)
        fetchInitialMessages()
      } else {
        setAuthError(resp.data.message || t('page.chat.msg.set_password_failed'))
      }
    } catch (e) {
      setAuthError(t('page.chat.msg.network_retry'))
    } finally {
      setIsSettingPassword(false)
    }
  }

  const handleLogin = async (e?: React.FormEvent) => {
    e?.preventDefault()
    setIsLoggingIn(true)
    setAuthError('')
    try {
      const resp = await api.post('/chat/sessions', {
        player_id: loginPlayerId,
        password: loginPassword
      })
      const d = unwrapData<{ session_id?: string; uuid?: string }>(resp)
      if (resp.data.status === 'success' && d?.session_id) {
        setIsLoggedIn(true)
        setCurrentPlayer(loginPlayerId)
        setCurrentPlayerUuid(d.uuid || '')
        localStorage.setItem('chat_session_id', d.session_id)
        fetchInitialMessages()
      } else {
        setAuthError(resp.data.message || t('page.chat.msg.login_failed'))
      }
    } catch (e: unknown) {
      const err = e as { response?: { data?: { message?: string } } }
      setAuthError(err.response?.data?.message || t('page.chat.msg.network_retry'))
    } finally {
      setIsLoggingIn(false)
    }
  }

  const handleLogout = () => {
    localStorage.removeItem('chat_session_id')
    setIsLoggedIn(false)
    setInitialMessagesLoaded(false)
    setCurrentPlayer('')
    setCurrentPlayerUuid('')
    setChatMessages([])
  }

  const handleSendMessage = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!chatMessage.trim() || isSending) return

    const now = Date.now()
    if (now - lastSendAtMs < 2000) return

    setIsSending(true)
    try {
      const sessionId = localStorage.getItem('chat_session_id')
      const resp = await api.post('/chat/messages', {
        message: chatMessage.trim(),
        player_id: currentPlayer,
        session_id: sessionId
      })
      if (resp.data.status === 'success') {
        setChatMessage('')
        setLastSendAtMs(Date.now())
        loadNewMessages()
      } else {
        notify(resp.data.message || t('page.chat.msg.send_failed'))
      }
    } catch (e) {
      notify(t('page.chat.msg.network_send_failed'))
    } finally {
      setIsSending(false)
    }
  }

  const getAvatarUrl = (name: string, uuid?: string) => {
    if (avatarSource === 'mccag') {
      return `https://x.xzt.plus/api/generate/minimal/mojang/${name}`
    }
    // MCHeads: use UUID when available for stable avatar (https://mc-heads.net/avatar/{uuid}/64)
    return `https://mc-heads.net/avatar/${uuid || name}/64`
  }

  const renderMessageContent = (msg: ChatMessage): React.ReactNode => {
    if (msg.message_source === 'webui') return msg.message
    if (msg.is_rtext && msg.rtext_data) {
      return parseRText(msg.rtext_data, {
        onCommandClick: async (command: string) => {
          // 执行命令：发送到服务器
          try {
            const sessionId = localStorage.getItem('chat_session_id')
            await api.post('/chat/messages', {
              message: command,
              player_id: currentPlayer,
              session_id: sessionId
            })
            loadNewMessages()
          } catch (e) {
            console.error('执行命令失败:', e)
          }
        },
        onCommandSuggest: (command: string) => {
          // 建议命令：填充到输入框
          setChatMessage(command)
        }
      })
    }
    return msg.message
  }

  const formatMessageDateTime = (ts: number) => {
    const d = new Date(ts * 1000)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    return `${y}/${m}/${day} ${time}`
  }

  // 同名玩家（同时游戏内 + 网页在线）合并为一条；公开聊天页不展示假人
  const mergedOnlineMembers = mergeOnlineMembers(onlineStatus, { includeBots: false })
  const onlinePlayerCount = countOnlinePlayers(onlineStatus)

  return (
    <div className="h-screen w-screen bg-slate-50 dark:bg-slate-950 flex items-center justify-center md:p-4 overflow-hidden relative">
      <div className="absolute top-0 right-0 -mr-48 -mt-48 w-96 h-96 bg-blue-500/10 rounded-full blur-xl pointer-events-none" />
      <div className="absolute bottom-0 left-0 -ml-48 -mb-48 w-96 h-96 bg-purple-500/10 rounded-full blur-xl pointer-events-none" />

      {isLoggedIn ? (
        /* 
           FIX: We inline the ChatUI here. 
           Defining ChatUI as a function/component inside PlayerChat caused constant remounting 
           on every render because the component type was technically "new" every time.
        */
        <div className="flex flex-col h-full md:h-[calc(100vh-64px)] w-full max-w-7xl mx-auto bg-white dark:bg-slate-900 
          md:rounded-[2.5rem] md:border md:border-slate-200 md:dark:border-slate-800 
          shadow-2xl overflow-hidden relative transition-all duration-300">

          {/* Header */}
          <header className="px-4 md:px-8 py-3 md:py-5 border-b border-slate-100 dark:border-slate-800 
            flex items-center justify-between bg-white/95 dark:bg-slate-900/95 z-20">
            <div className="flex items-center gap-3 md:gap-4">
              <div className="w-10 h-10 md:w-12 md:h-12 rounded-xl md:rounded-2xl overflow-hidden shrink-0 shadow-sm border border-white dark:border-slate-800">
                <img
                  src={getAvatarUrl(currentPlayer, currentPlayerUuid)}
                  className={`w-full h-full object-cover ${avatarSource === 'mccag' ? 'scale-125' : ''}`}
                  alt="avatar"
                />
              </div>
              <div className="min-w-0">
                <h3 className="text-base md:text-lg font-black truncate text-slate-900 dark:text-white">{currentPlayer}</h3>
                <div className="flex items-center gap-2 text-[10px] md:text-xs text-slate-500 font-bold uppercase tracking-wider">
                  <span className={`w-2 h-2 rounded-full ${serverStatus.status === 'running' ? 'bg-emerald-500' : 'bg-slate-400'}`} />
                  {serverStatus.players || (onlinePlayerCount > 0 ? `${onlinePlayerCount}/?` : '0/0')} {t('page.chat.header.players_online')}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-1 md:gap-2">
              <button onClick={() => setShowOnlinePanel(!showOnlinePanel)} className={`p-2 md:p-3 rounded-xl transition-colors ${showOnlinePanel ? 'bg-blue-600 text-white' : 'text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800'}`}>
                <Users size={20} />
              </button>
              <button onClick={() => setShowSettings(!showSettings)} className={`p-2 md:p-3 rounded-xl transition-colors ${showSettings ? 'bg-blue-600 text-white' : 'text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800'}`}>
                <Settings size={20} />
              </button>
              <button onClick={handleLogout} className="p-2 md:p-3 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-xl transition-colors">
                <LogOut size={20} />
              </button>
            </div>
          </header>

          <div className="flex flex-1 overflow-hidden">
            {/* Messages */}
            <div className="flex-1 flex flex-col min-w-0 bg-slate-50/30 dark:bg-slate-950/20 relative">
              <div
                ref={chatContainerRef}
                className={`flex-1 overflow-y-auto p-4 md:p-8 scroll-smooth custom-scrollbar ${chatDisplayMode === 'mc' ? 'space-y-0' : 'space-y-4 md:space-y-6'}`}
              >
                {hasMoreMessages && (
                  <button
                    onClick={() => loadChatMessages(50, Math.min(...chatMessages.map(m => m.id)))}
                    disabled={isLoadingMessages}
                    className="w-full py-2 text-xs font-bold text-slate-400 hover:text-blue-500 transition-colors flex items-center justify-center gap-2"
                  >
                    {isLoadingMessages ? <Loader2 className="animate-spin w-4 h-4" /> : <RefreshCw className="w-4 h-4" />}
                    {t('page.chat.room.load_more')}
                  </button>
                )}

                {isLoadingMessages && chatMessages.length === 0 ? (
                  Array.from({ length: 8 }).map((_, i) => (
                    <MessageLineSkeleton key={i} />
                  ))
                ) : chatDisplayMode === 'mc' ? (
                  chatMessages.slice().reverse().map((msg, i) => (
                    <div key={`${msg.id}-${i}`} className="flex items-baseline gap-1.5 py-0.5 font-mono text-sm">
                      <span className="text-slate-500 shrink-0">{formatMessageDateTime(msg.timestamp)}</span>
                      <span className={`font-semibold shrink-0 ${msg.player_id === currentPlayer ? 'text-blue-500 dark:text-blue-400' : 'text-amber-600 dark:text-amber-500'}`}>
                        {msg.player_id}:
                      </span>
                      <span className="text-slate-700 dark:text-slate-200 break-words">{renderMessageContent(msg)}</span>
                    </div>
                  ))
                ) : (
                  chatMessages.slice().reverse().map((msg, i) => {
                    const isMe = msg.player_id === currentPlayer
                    const showHeader = i === 0 || chatMessages.slice().reverse()[i - 1].player_id !== msg.player_id
                    return (
                      <div key={`${msg.id}-${i}`} className={`flex gap-3 md:gap-4 ${isMe ? 'flex-row-reverse' : 'flex-row'}`}>
                        <div className={`w-8 h-8 md:w-10 md:h-10 rounded-lg md:rounded-xl overflow-hidden shrink-0 shadow-sm ${showHeader ? 'opacity-100' : 'opacity-0'}`}>
                          <img
                            src={getAvatarUrl(msg.player_id, msg.uuid)}
                            className={`w-full h-full object-cover ${avatarSource === 'mccag' ? 'scale-125' : ''}`}
                            alt=""
                          />
                        </div>
                        <div className={`flex flex-col max-w-[85%] md:max-w-[70%] ${isMe ? 'items-end' : 'items-start'}`}>
                          {showHeader && (
                            <div className="flex items-center gap-2 mb-1 px-1">
                              <span className="text-[10px] md:text-xs font-black text-slate-400 uppercase tracking-wider">{msg.player_id}</span>
                              <span className="text-[10px] text-slate-300 font-medium">{formatMessageDateTime(msg.timestamp)}</span>
                            </div>
                          )}
                          <div className={`px-3 py-2 md:px-4 md:py-2.5 rounded-2xl text-sm md:text-base leading-relaxed shadow-sm transition-all
                            ${isMe
                              ? 'bg-blue-600 text-white rounded-tr-none'
                              : 'bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 border border-slate-100 dark:border-slate-700/50 rounded-tl-none'}`}>
                            {renderMessageContent(msg)}
                          </div>
                        </div>
                      </div>
                    )
                  })
                )}
              </div>

              {/* Input Area */}
              <div className="p-3 md:p-6 bg-white dark:bg-slate-900 border-t border-slate-100 dark:border-slate-800">
                <form onSubmit={handleSendMessage} className="flex gap-2 md:gap-3">
                  <input
                    type="text"
                    value={chatMessage}
                    onChange={(e) => setChatMessage(e.target.value)}
                    placeholder={t('page.chat.room.input_placeholder_send')}
                    className="flex-1 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-xl md:rounded-2xl px-4 py-3 
                      focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none text-sm md:text-base transition-all"
                  />
                  <button
                    type="submit"
                    disabled={!chatMessage.trim() || isSending}
                    className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white p-3 md:px-8 rounded-xl md:rounded-2xl 
                      shadow-lg shadow-blue-500/30 transition-all flex items-center justify-center"
                  >
                    {isSending ? <Loader2 className="animate-spin" size={20} /> : <Send size={20} className="md:mr-2" />}
                    <span className="hidden md:inline font-black uppercase tracking-widest text-sm">{t('page.chat.room.send.submit')}</span>
                  </button>
                </form>
              </div>
            </div>

            {/* Online List */}
            <AnimatePresence>
              {showOnlinePanel && (
                <motion.div
                  initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
                  transition={{ type: 'spring', damping: 25, stiffness: 200 }}
                  className="absolute inset-y-0 right-0 w-72 bg-white dark:bg-slate-900 border-l border-slate-100 
                    dark:border-slate-800 z-30 shadow-2xl md:relative md:shadow-none flex flex-col"
                >
                  <div className="p-6 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
                    <h4 className="font-black uppercase tracking-tighter text-lg">{t('page.chat.panel.online_members')}</h4>
                    <button onClick={() => setShowOnlinePanel(false)} className="md:hidden p-2"><ChevronLeft /></button>
                  </div>
                  <div className="flex-1 overflow-y-auto p-4 space-y-4 custom-scrollbar">
                    {/* 合并渲染：同一玩家在游戏内与网页同时在线时只显示一条 */}
                    {mergedOnlineMembers.map(member => (
                      <PlayerListItem
                        key={member.name}
                        name={member.name}
                        uuid={playerUuidMap[member.name]}
                        source={avatarSource}
                        status={member.statuses[0]}
                        statuses={member.statuses}
                      />
                    ))}
                    {Object.entries(offlineMembers)
                      .filter(([, info]) => (Math.floor(Date.now() / 1000) - info.lastSeen) <= OFFLINE_HIDE_SECONDS)
                      .map(([name, info]) => (
                        <PlayerListItem key={name} name={name} uuid={info.uuid} source={avatarSource} status="offline" lastSeen={info.lastSeen} t={t} />
                      ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Settings Modal */}
          <AnimatePresence>
            {showSettings && (
              <motion.div
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                className="absolute inset-0 bg-slate-900/60 z-40 flex items-center justify-center p-4"
              >
                <motion.div
                  initial={{ scale: 0.9, y: 20 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.9, y: 20 }}
                  className="bg-white dark:bg-slate-900 w-full max-w-md rounded-[2.5rem] p-8 shadow-2xl space-y-8"
                >
                  <div className="flex items-center justify-between">
                    <h3 className="text-2xl font-black text-slate-900 dark:text-white uppercase tracking-tighter">{t('page.chat.settings.modal_title')}</h3>
                    <button onClick={() => setShowSettings(false)} className="p-2 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-xl"><X size={24} /></button>
                  </div>

                  <div className="space-y-6">
                    <div className="space-y-3">
                      <label className="text-xs font-black text-slate-400 uppercase tracking-widest ml-1">{t('page.chat.settings.display_mode_label')}</label>
                      <div className="grid grid-cols-2 gap-2 p-1 bg-slate-100 dark:bg-slate-800 rounded-2xl">
                        <button
                          onClick={() => { setChatDisplayMode('modern'); localStorage.setItem('chat_display_mode', 'modern'); }}
                          className={`py-2 text-xs font-bold rounded-xl transition-all ${chatDisplayMode === 'modern' ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-500'}`}
                        >
                          {t('page.chat.settings.display_modern')}
                        </button>
                        <button
                          onClick={() => { setChatDisplayMode('mc'); localStorage.setItem('chat_display_mode', 'mc'); }}
                          className={`py-2 text-xs font-bold rounded-xl transition-all ${chatDisplayMode === 'mc' ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-500'}`}
                        >
                          {t('page.chat.settings.display_mc')}
                        </button>
                      </div>
                    </div>
                    <div className="space-y-3">
                      <label className="text-xs font-black text-slate-400 uppercase tracking-widest ml-1">{t('page.chat.settings.avatar_source_label')}</label>
                      <div className="grid grid-cols-2 gap-2 p-1 bg-slate-100 dark:bg-slate-800 rounded-2xl">
                        <button
                          onClick={() => { setAvatarSource('mccag'); localStorage.setItem('chat_avatar_source', 'mccag'); }}
                          className={`py-2 text-xs font-bold rounded-xl transition-all ${avatarSource === 'mccag' ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-500'}`}
                        >
                          {t('page.chat.settings.avatar_mccag')}
                        </button>
                        <button
                          onClick={() => { setAvatarSource('mcheads'); localStorage.setItem('chat_avatar_source', 'mcheads'); }}
                          className={`py-2 text-xs font-bold rounded-xl transition-all ${avatarSource === 'mcheads' ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-500'}`}
                        >
                          {t('page.chat.settings.avatar_mcheads')}
                        </button>
                      </div>
                    </div>
                  </div>

                  <button
                    onClick={() => setShowSettings(false)}
                    className="w-full py-4 bg-slate-900 dark:bg-white text-white dark:text-slate-900 font-bold rounded-2xl shadow-xl transition-all"
                  >
                    {t('common.close')}
                  </button>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      ) : (
        /* ==================== Auth UI ==================== */
        <div className="w-full max-w-md mx-auto p-6 animate-in fade-in slide-in-from-bottom-8 duration-500">
          <div className="bg-white dark:bg-slate-900 p-8 rounded-[2rem] border border-slate-200 dark:border-slate-800 shadow-2xl space-y-8">
            <div className="text-center space-y-2">
              <div className="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-2xl text-white shadow-xl shadow-blue-500/30 mb-4">
                <MessageSquare className="w-8 h-8" />
              </div>
              <h2 className="text-2xl md:text-3xl font-black text-slate-900 dark:text-white tracking-tight">
                {t('page.chat.header_title')}
              </h2>
              <p className="text-slate-500 dark:text-slate-400 font-medium text-xs md:text-sm">
                {t('page.chat.room.desc')}
              </p>
            </div>

            <div className="flex p-1 bg-slate-100 dark:bg-slate-800 rounded-2xl">
              <button
                onClick={() => setAuthTab('login')}
                className={`flex-1 py-2.5 text-sm font-bold rounded-xl transition-all ${authTab === 'login' ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-500'}`}
              >
                {t('page.chat.tabs.direct_login')}
              </button>
              <button
                onClick={() => setAuthTab('verify')}
                className={`flex-1 py-2.5 text-sm font-bold rounded-xl transition-all ${authTab === 'verify' ? 'bg-white dark:bg-slate-700 text-blue-600 shadow-sm' : 'text-slate-500'}`}
              >
                {t('page.chat.tabs.verify_flow')}
              </button>
            </div>

            {authError && (
              <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} className="bg-rose-50 dark:bg-rose-900/20 border border-rose-100 dark:border-rose-900/30 text-rose-600 dark:text-rose-400 px-4 py-3 rounded-xl text-sm font-medium">
                {authError}
              </motion.div>
            )}

            {authTab === 'login' ? (
              <form className="space-y-6" onSubmit={handleLogin}>
                <div className="space-y-4">
                  <div className="space-y-1.5">
                    <label className="block text-sm font-bold text-slate-700 dark:text-slate-300 ml-1">
                      {t('page.chat.login.player_id_label')}
                    </label>
                    <div className="relative group">
                      <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400 group-focus-within:text-blue-500 transition-colors" />
                      <input
                        type="text"
                        value={loginPlayerId}
                        onChange={(e) => setLoginPlayerId(e.target.value)}
                        placeholder={t('page.chat.login.player_id_placeholder')}
                        className="w-full pl-11 pr-4 py-3 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-2xl outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all"
                      />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <label className="block text-sm font-bold text-slate-700 dark:text-slate-300 ml-1">
                      {t('page.chat.login.password_label')}
                    </label>
                    <div className="relative group">
                      <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400 group-focus-within:text-blue-500 transition-colors" />
                      <input
                        type="password"
                        value={loginPassword}
                        onChange={(e) => setLoginPassword(e.target.value)}
                        placeholder={t('page.chat.login.password_placeholder')}
                        className="w-full pl-11 pr-4 py-3 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-2xl outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all"
                      />
                    </div>
                  </div>
                </div>
                <button
                  disabled={isLoggingIn}
                  type="submit"
                  className="w-full py-4 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-2xl shadow-lg shadow-blue-500/30 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  {isLoggingIn ? <Loader2 className="animate-spin w-5 h-5" /> : <Zap className="w-5 h-5" />}
                  {t('page.chat.login.submit')}
                </button>
              </form>
            ) : (
              <div className="space-y-6">
                <div className="flex justify-between relative px-2">
                  {[1, 2, 3].map((step) => {
                    const canClick = step <= currentStep
                    return (
                      <div key={step} className="flex flex-col items-center z-10">
                        <button
                          type="button"
                          disabled={!canClick}
                          onClick={() => canClick && setCurrentStep(step as 1 | 2 | 3)}
                          className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-all ${
                            currentStep >= step
                              ? 'bg-blue-600 border-blue-600 text-white'
                              : 'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800 text-slate-400'
                          } ${canClick ? 'cursor-pointer hover:opacity-90' : 'cursor-not-allowed opacity-60'}`}
                          aria-label={`chat-step-${step}`}
                        >
                          {step}
                        </button>
                      </div>
                    )
                  })}
                  <div className="absolute top-4 left-0 w-full h-0.5 bg-slate-100 dark:bg-slate-800 -z-0" />
                </div>

                <AnimatePresence mode="wait">
                  {currentStep === 1 && (
                    <motion.div key="step1" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="space-y-4">
                      <div className="p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800/50 rounded-2xl text-xs leading-relaxed text-blue-700 dark:text-blue-300">
                        {t('page.chat.get_code.desc_before')}
                        <code className="mx-1 px-1.5 py-0.5 bg-blue-100 dark:bg-blue-900/40 rounded font-mono font-bold italic">!!webui verify</code>
                        {t('page.chat.get_code.desc_after')}
                      </div>
                      <button
                        onClick={handleGenerateCode}
                        disabled={isGenerating}
                        className="w-full py-4 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-2xl shadow-lg shadow-blue-500/30 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
                      >
                        {isGenerating ? <Loader2 className="animate-spin w-5 h-5" /> : <RefreshCw className="w-5 h-5" />}
                        {t('page.chat.get_code.generate')}
                      </button>
                    </motion.div>
                  )}
                  {currentStep === 2 && (
                    <motion.div key="step2" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="space-y-6">
                      <div className="text-center space-y-2">
                        <p className="text-xs font-bold text-slate-400">{t('page.chat.get_code.your_code')}</p>
                        <div className="text-3xl font-black tracking-[0.3em] text-blue-600 dark:text-blue-400 bg-slate-50 dark:bg-slate-800/50 py-4 rounded-2xl border-2 border-dashed border-blue-100 dark:border-blue-900/30">
                          {verificationCode}
                        </div>
                        <div className="space-y-3">
                          <p className="text-xs font-bold text-slate-400">{t('page.chat.get_code.full_command')}</p>
                          <div className="flex items-center justify-center gap-2">
                            <code className="flex-1 px-3 py-2 bg-slate-100 dark:bg-slate-800/50 rounded-xl font-mono text-xs text-slate-800 dark:text-slate-200 text-left break-all">
                              {`!!webui verify ${verificationCode}`}
                            </code>
                            <button
                              type="button"
                              onClick={() => copyText(`!!webui verify ${verificationCode}`)}
                              className="shrink-0 px-3 py-2 text-xs font-bold bg-blue-600 hover:bg-blue-700 text-white rounded-xl shadow-sm transition-all disabled:opacity-50"
                              disabled={!verificationCode}
                              aria-label="copy-full-command"
                            >
                              {t('page.chat.get_code.copy_command')}
                            </button>
                          </div>
                          {copyFeedback ? (
                            <p className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">
                              {copyFeedback}
                            </p>
                          ) : null}
                        </div>
                      </div>
                      <button onClick={handleCheckVerification} disabled={isChecking} className="w-full py-4 bg-blue-600 text-white font-bold rounded-2xl shadow-lg transition-all flex items-center justify-center gap-2">
                        {isChecking ? <Loader2 className="animate-spin w-5 h-5" /> : <ShieldCheck className="w-5 h-5" />}
                        {t('page.chat.verify.check')}
                      </button>
                    </motion.div>
                  )}
                  {currentStep === 3 && (
                    <motion.div key="step3" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="space-y-4">
                      <input
                        type="password"
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        placeholder={t('page.chat.set_password.password_placeholder')}
                        className="w-full px-4 py-3 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl outline-none focus:ring-2 focus:ring-blue-500/20"
                      />
                      <input
                        type="password"
                        value={confirmPassword}
                        onChange={(e) => setConfirmPassword(e.target.value)}
                        placeholder={t('page.chat.set_password.confirm_placeholder')}
                        className="w-full px-4 py-3 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl outline-none focus:ring-2 focus:ring-blue-500/20"
                      />
                      <button onClick={handleSetPassword} disabled={isSettingPassword} className="w-full py-4 bg-blue-600 text-white font-bold rounded-2xl shadow-lg transition-all flex items-center justify-center gap-2">
                        {isSettingPassword ? <Loader2 className="animate-spin w-5 h-5" /> : <Lock className="w-5 h-5" />}
                        {t('page.chat.set_password.submit')}
                      </button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}
          </div>
          <VersionFooter className="mt-8 text-center" />
        </div>
      )}
    </div>
  )
}

// PlayerListItem is defined outside to ensure stable reference
const PlayerListItem: React.FC<{ name: string; uuid?: string; source: string; status: OnlineMemberStatus | 'offline'; statuses?: OnlineMemberStatus[]; lastSeen?: number; t?: (key: string, options?: Record<string, unknown>) => string }> = ({ name, uuid, source, status, statuses, lastSeen, t }) => {
  const getAvatar = () => {
    if (source === 'mccag') return `https://x.xzt.plus/api/generate/minimal/mojang/${name}`
    // MCHeads: use UUID when available (https://mc-heads.net/avatar/{uuid}/40)
    return `https://mc-heads.net/avatar/${uuid || name}/40`
  }

  const formatOfflineTime = (ts: number) => {
    const now = Math.floor(Date.now() / 1000)
    const diff = now - ts
    if (diff < 60) return t?.('page.chat.offline.just_offline') ?? '刚刚'
    if (diff < 3600) return t?.('page.chat.offline.minutes_ago_format', { count: Math.floor(diff / 60) }) ?? `${Math.floor(diff / 60)}分钟前`
    return new Date(ts * 1000).toLocaleDateString()
  }

  // 合并在线时同时展示全部来源（如“游戏中 · 网页”）
  const activeStatuses: OnlineMemberStatus[] = statuses && statuses.length > 0
    ? statuses
    : status === 'offline'
      ? []
      : [status]

  const statusText = (value: OnlineMemberStatus) =>
    value === 'game'
      ? (t?.('page.chat.offline.status_game') ?? 'game')
      : value === 'web'
        ? (t?.('page.chat.offline.status_web') ?? 'web')
        : 'bot'

  const statusLabel = status === 'offline'
    ? (lastSeen ? formatOfflineTime(lastSeen) : 'offline')
    : activeStatuses.map(statusText).join(' · ')

  // 状态点颜色优先级：游戏内 > 网页 > 假人
  const statusDotClass = activeStatuses.includes('game')
    ? 'bg-emerald-500'
    : activeStatuses.includes('web')
      ? 'bg-blue-500'
      : status === 'offline'
        ? 'bg-slate-300 dark:bg-slate-600'
        : 'bg-purple-500'

  return (
    <div className="flex items-center gap-3 p-2 rounded-2xl hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors group">
      <div className="relative shrink-0 w-10 h-10 rounded-xl overflow-hidden shadow-sm border border-white dark:border-slate-800">
        <img src={getAvatar()} alt={name} className={`w-full h-full object-cover ${source === 'mccag' ? 'scale-125' : ''}`} />
        <div className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 border-2 border-white dark:border-slate-900 rounded-full ${statusDotClass}`} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-bold text-slate-800 dark:text-slate-200 truncate">{name}</p>
        <p className="text-[10px] font-black text-slate-400 uppercase tracking-tighter">
          {statusLabel}
        </p>
      </div>
    </div>
  )
}

export default PlayerChat