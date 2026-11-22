import { useState, useRef, useEffect } from 'react'
import axios from 'axios'
import './App.css'

// API base URL - uses environment variable in production, /api proxy in development
const API_BASE_URL = import.meta.env.VITE_API_URL || '/api'

// Storage key for progress logs
const STORAGE_KEY = 'carv_progress_logs'

// Login: the backend checks the password and returns a token, kept on this device for 30 days
const TOKEN_KEY = 'carv_login_token'
const readToken = () => { try { return localStorage.getItem(TOKEN_KEY) } catch { return null } }
const writeToken = (token) => {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // storage unavailable (private mode): the token lasts until reload
  }
}

// Send the login token with every API request
axios.interceptors.request.use((config) => {
  const token = readToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Local wall-clock time as "YYYY-MM-DDTHH:MM" (the datetime-local input format).
// toISOString() would convert to UTC and shift the session by the timezone offset.
const toLocalInputValue = (date = new Date()) => {
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

// Normalise a datetime string to "YYYY-MM-DDTHH:MM" local time without timezone shifting.
// Naive strings (from CARV screenshots, EXIF, filenames) are kept as written.
const normaliseLocalDateTime = (value) => {
  if (!value) return null
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) && !/(Z|[+-]\d{2}:?\d{2})$/.test(value)) {
    return value.slice(0, 16)
  }
  const parsed = new Date(value)
  return isNaN(parsed) ? null : toLocalInputValue(parsed)
}

// Do a database session and a local log describe the same saved session?
// Older saves sent UTC ("...Z") times that the backend stored without a timezone,
// so allow a whole-hour offset when the Ski:IQ also matches.
const isSameSessionMoment = (dbSession, log) => {
  const diff = Math.abs(new Date(dbSession.session_date).getTime() - new Date(log.datetime).getTime())
  if (isNaN(diff)) return false
  if (diff < 60000) return true
  const hourMs = 3600000
  const offByWholeHours = diff <= 14 * hourMs && (diff % hourMs < 60000 || hourMs - (diff % hourMs) < 60000)
  const sameSkiIQ = dbSession.ski_iq != null && log.metrics?.skiIQ != null &&
    Math.round(dbSession.ski_iq) === Math.round(log.metrics.skiIQ)
  return offByWholeHours && sameSkiIQ
}

// Convert a database session into the progress-log shape used by the UI
const dbSessionToLog = (session) => {
  const metrics = session.metrics || {}
  const overall = metrics.overall_metrics || {}
  return {
    id: session.id,
    dbSessionId: session.id,
    datetime: session.session_date,
    datetimeDisplay: metrics.session_overview?.session_date_display || null,
    datetimeSource: 'database',
    notes: session.notes || '',
    savedAt: session.created_at || session.session_date,
    analysis: session.metrics || null,
    trainingPlan: parseStoredPlan(session.training_plan),
    metrics: {
      skiIQ: session.ski_iq ?? metrics.session_overview?.ski_iq_range?.average ?? null,
      balance: overall.balance || null,
      edging: overall.edging || null,
      rotary: overall.rotary || null,
      performance: overall.performance || null
    }
  }
}

// Link local logs to their database copies and add database sessions missing locally
const mergeDbSessionsIntoLogs = (localLogs, sessions) => {
  const claimed = new Set(localLogs.map(log => log.dbSessionId).filter(Boolean))
  const merged = localLogs.map(log => {
    if (log.dbSessionId) return log
    const match = sessions.find(s => !claimed.has(s.id) && (s.id === log.id || isSameSessionMoment(s, log)))
    if (!match) return log
    claimed.add(match.id)
    return { ...log, dbSessionId: match.id }
  })
  sessions.forEach(s => {
    if (!claimed.has(s.id)) merged.push(dbSessionToLog(s))
  })
  return merged
}

// The Ski:IQ Metrics list as the Carv app shows it, in Carv's order and units.
// Mirrors CARV_METRICS in backend/app/carv_metrics.py.
const CARV_METRIC_DEFS = [
  { key: 'parallel_skis', label: 'Parallel Skis', unit: '%', legacy: ['rotary', 'parallel_skis'] },
  { key: 'turn_shape', label: 'Turn Shape', unit: '%', legacy: ['rotary', 'turn_shape'] },
  { key: 'turn_closure', label: 'Turn Closure', unit: '%', zoneOnly: true, legacy: ['rotary', 'turn_closure'] },
  { key: 'early_edging', label: 'Early Edging', unit: '%', legacy: ['edging', 'early_edging'] },
  { key: 'edging_smoothness', label: 'Edging Smoothness', unit: '%', legacy: ['edging', 'edging_smoothness'] },
  { key: 'edging_similarity', label: 'Edging Similarity', unit: '%', legacy: ['edging', 'edging_similarity'] },
  { key: 'edge_build', label: 'Edge Build', unit: '%', legacy: ['edging', 'progressive_edge_build'] },
  { key: 'edge_angle', label: 'Edge Angle', unit: '°', legacy: ['edging', 'edge_angle'] },
  { key: 'weight_release', label: 'Weight Release', unit: '%', legacy: ['balance', 'transition_weight_release'] },
  { key: 'g_force', label: 'G-Force', unit: 'g', legacy: ['performance', 'turn_g_force'] },
  { key: 'slope_pitch', label: 'Slope Pitch', unit: '°' }
]

const ZONE_LABELS = { below: 'Below zone', in: 'In zone', above: 'Above zone' }

// Ski:IQ 26 "Stelvio" was announced on this date (Carv app 10)
const STELVIO_RELEASE_DATE = '2026-11-09'
const SKI_IQ_VERSION_LABELS = { stelvio: 'Ski:IQ 26', wasatch: 'Pre-26' }

// Which Ski:IQ scoring an analysis was scored with: as read from the screenshots
// (or set by hand), otherwise guessed from the session date
const getSkiIQVersion = (analysis, fallbackDate = null) => {
  const version = analysis?.session_overview?.ski_iq_version
  if (version === 'stelvio' || version === 'wasatch') return version
  const date = normaliseLocalDateTime(analysis?.session_overview?.session_datetime || fallbackDate)
  if (!date) return null
  return date.slice(0, 10) >= STELVIO_RELEASE_DATE ? 'stelvio' : 'wasatch'
}

const formatMetricValue = (value, unit) => {
  if (value === null || value === undefined) return '-'
  if (unit === 'g') return `${Number(value).toFixed(1)} g`
  return `${Math.round(value)}${unit}`
}

// A Carv metric's value from a progress log: the Carv metrics list when present,
// otherwise the older grouped fields saved by earlier versions of the app
const getLogMetricValue = (log, def) => {
  const carvValue = log?.analysis?.carv_metrics?.[def.key]?.value
  if (carvValue !== null && carvValue !== undefined) return carvValue
  if (!def.legacy) return null
  const [category, key] = def.legacy
  const value = log?.metrics?.[category]?.[key] ?? log?.analysis?.overall_metrics?.[category]?.[key]
  return value === undefined ? null : value
}

// Structured training plans (from /generate-plan). Older plans are markdown strings.
const PLAN_FORMAT = 'carv-plan-v1'

// A training plan from state, localStorage or the database: a structured plan object,
// or the original markdown string for plans made before structured plans existed
const parseStoredPlan = (value) => {
  if (!value) return null
  if (typeof value === 'object') return value.format === PLAN_FORMAT ? value : null
  if (typeof value === 'string' && value.trimStart().startsWith('{')) {
    try {
      const parsed = JSON.parse(value)
      if (parsed?.format === PLAN_FORMAT) return parsed
    } catch {
      // not JSON: an old markdown plan
    }
  }
  return value
}

// The database column is text, so structured plans are stored as JSON
const serializePlan = (plan) => (plan && typeof plan === 'object' ? JSON.stringify(plan) : plan || null)

const isStructuredPlan = (plan) => !!plan && typeof plan === 'object' && plan.format === PLAN_FORMAT

const drillName = (drill) => drill?.reference?.name || drill?.drill_id || 'Drill'

// A ski day built from the plan's three drills: warm-up, a block per drill, integration, a fun run
const buildDailySchedule = (plan) => {
  const totalRuns = plan.runs_per_day || 10
  const drills = plan.drills || []
  const warmUp = totalRuns >= 8 ? 2 : 1
  const drillRuns = Math.max(drills.length, totalRuns - warmUp - 2)
  const perDrill = drills.map((_, idx) => Math.floor(drillRuns / drills.length) + (idx < drillRuns % drills.length ? 1 : 0))
  const phases = []
  let run = 1
  const span = (count) => {
    const label = count > 1 ? `${run}-${run + count - 1}` : `${run}`
    run += count
    return label
  }
  phases.push({ runs: span(warmUp), phase: 'Warm-up', details: ['Free skiing at about 70% effort', 'Get loose and feel the snow'] })
  drills.forEach((drill, idx) => {
    phases.push({
      runs: span(perDrill[idx]),
      phase: drillName(drill),
      details: [
        `${drill.focused_turns || 4} focused turns, then free ski to the bottom`,
        drill.mental_cue ? `Cue: "${drill.mental_cue}"` : null,
        idx > 0 ? 'Connect the feeling to the drill before' : 'Rest at the bottom and think about the feel'
      ].filter(Boolean)
    })
  })
  phases.push({ runs: span(1), phase: 'Integration run', details: ['Free skiing at about 80% effort', 'Let the drills show up without thinking'] })
  phases.push({ runs: span(1), phase: 'Fun run', details: ['Ski for enjoyment', 'Notice what feels different'] })
  return phases
}

// A training week built from the plan's three drills, sized to the days available
const buildWeeklySchedule = (plan) => {
  const [primary, secondary, integration] = (plan.drills || []).map(drillName)
  const templates = {
    foundation: { name: 'Foundation', primaryFocus: `${primary} (about 4 runs)`, secondary: `${secondary} (about 2 runs)`, terrain: 'Easier runs, perfect technique', goal: 'Establish the movement pattern' },
    development: { name: 'Development', primaryFocus: `${secondary} (about 4 runs)`, secondary: `${primary} review (2 runs), introduce ${integration}`, terrain: 'Moderate terrain', goal: 'Build on day one and add complexity' },
    integration: { name: 'Integration', primaryFocus: `All three drills, about 2 runs each`, secondary: 'Several free runs applying them', terrain: 'Varied pitch', goal: 'Connect everything and build confidence' },
    challenge: { name: 'Challenge', primaryFocus: 'One run of each drill as a warm-up', secondary: 'Apply them on steeper or faster terrain', terrain: 'Steeper terrain', goal: 'Keep the technique under pressure' },
    recovery: { name: 'Recovery and check', primaryFocus: 'Your favourite drill (2-3 runs)', secondary: 'Mostly free skiing with awareness', terrain: 'Comfortable runs', goal: 'Take new Carv screenshots to compare' }
  }
  const orders = {
    1: ['integration'],
    2: ['foundation', 'integration'],
    3: ['foundation', 'integration', 'recovery'],
    4: ['foundation', 'development', 'integration', 'recovery'],
    5: ['foundation', 'development', 'integration', 'challenge', 'recovery'],
    6: ['foundation', 'development', 'integration', 'integration', 'challenge', 'recovery'],
    7: ['foundation', 'development', 'integration', 'challenge', 'integration', 'challenge', 'recovery']
  }
  const days = Math.min(7, Math.max(1, plan.days_available || 5))
  return orders[days].map((key, idx) => ({ dayNum: idx + 1, ...templates[key] }))
}

// Plain-text version of a structured plan for the text export
const planToText = (plan) => {
  if (!isStructuredPlan(plan)) return typeof plan === 'string' ? plan : ''
  const lines = []
  if (plan.summary) lines.push(plan.summary, '')
  if (plan.biggest_limiter) lines.push(`Biggest limiter: ${plan.biggest_limiter}`)
  if (plan.carv_alignment) lines.push(`Carv's view: ${plan.carv_alignment}`)
  if (plan.changes_since_last) lines.push(`Since last time: ${plan.changes_since_last}`)
  lines.push('')
  ;(plan.drills || []).forEach((drill, idx) => {
    lines.push(`DRILL ${idx + 1}: ${drillName(drill)} (${drill.role}) - target ${drill.target_metric}`)
    if (drill.why) lines.push(`  Why: ${drill.why}`)
    ;(drill.steps || []).forEach((step, sIdx) => lines.push(`  ${sIdx + 1}. ${step}`))
    lines.push(`  ${drill.runs} runs, ${drill.focused_turns} focused turns per run`)
    if (drill.success_feels_like) lines.push(`  Success feels like: ${drill.success_feels_like}`)
    if (drill.mental_cue) lines.push(`  Cue: "${drill.mental_cue}"`)
    ;(drill.common_mistakes || []).forEach(m => lines.push(`  Mistake: ${m.mistake} - fix: ${m.fix}`))
    if (drill.reference?.video_url) lines.push(`  Video: ${drill.reference.video_url}`)
    lines.push('')
  })
  ;(plan.priorities || []).forEach((p, idx) => lines.push(`Priority ${idx + 1}: ${p.metric} ${p.current} -> ${p.target}${p.drill_name ? ` (${p.drill_name})` : ''}`))
  return lines.join('\n')
}

function App() {
  // Auth state
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [passwordInput, setPasswordInput] = useState('')
  const [authError, setAuthError] = useState('')

  // State management
  const [selectedFiles, setSelectedFiles] = useState([]) // Array of {file, preview, id, name}
  const [analysis, setAnalysis] = useState(null) // Holistic analysis result
  const [trainingPlan, setTrainingPlan] = useState(null)
  const [loading, setLoading] = useState(false)
  const [planLoading, setPlanLoading] = useState(false)
  const [planDays, setPlanDays] = useState(5) // ski days this week, for the plan's schedule
  const [editingValues, setEditingValues] = useState(false) // correcting misread values
  const [loadedLogId, setLoadedLogId] = useState(null) // the saved session currently open, if any
  const [planRuns, setPlanRuns] = useState(10) // runs per ski day
  const [error, setError] = useState(null)
  const fileInputRef = useRef(null)

  // Progress logging state
  const [progressLogs, setProgressLogs] = useState([])
  const [showProgressPanel, setShowProgressPanel] = useState(false)
  const [sessionDateTime, setSessionDateTime] = useState('')
  const [sessionNotes, setSessionNotes] = useState('')
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [savingSession, setSavingSession] = useState(false)
  const [selectedLogForComparison, setSelectedLogForComparison] = useState(null)
  const [comparisonLogA, setComparisonLogA] = useState(null)
  const [comparisonLogB, setComparisonLogB] = useState(null)
  const [showComparisonModal, setShowComparisonModal] = useState(false)
  const [deleteConfirmId, setDeleteConfirmId] = useState(null)
  const [expandedLogId, setExpandedLogId] = useState(null) // For expandable log cards
  const [selectedMetric, setSelectedMetric] = useState('skiIQ') // For chart metric selection

  // Database-backed sessions state
  const [dbSessions, setDbSessions] = useState([])
  const [savingToDb, setSavingToDb] = useState(false)
  const [progressionAnalysis, setProgressionAnalysis] = useState(null)
  const [loadingProgression, setLoadingProgression] = useState(false)
  const [showProgressionModal, setShowProgressionModal] = useState(false)
  const [selectedSessionIds, setSelectedSessionIds] = useState([]) // For filtering progression analysis

  // Chat state
  const [showChat, setShowChat] = useState(false)
  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const chatMessagesEndRef = useRef(null)

  // Lets long-running loops (job polling) stop once the component is gone
  const isMountedRef = useRef(true)
  useEffect(() => {
    isMountedRef.current = true
    return () => { isMountedRef.current = false }
  }, [])

  // Signed in already on this device? (The server still checks the token on every request.)
  useEffect(() => {
    try { sessionStorage.removeItem('carv_auth') } catch { /* old browser-only login flag */ }
    if (readToken()) setIsAuthenticated(true)
  }, [])

  // Any 401 means the token is missing, expired or the password changed: back to the login screen
  useEffect(() => {
    const interceptor = axios.interceptors.response.use(
      (response) => response,
      (err) => {
        if (err.response?.status === 401 && !String(err.config?.url || '').endsWith('/login')) {
          writeToken(null)
          setIsAuthenticated(false)
          setAuthError(err.response.data?.message || 'Please sign in again')
        }
        return Promise.reject(err)
      }
    )
    return () => axios.interceptors.response.eject(interceptor)
  }, [])

  // Handle login: the backend checks the password
  const [loggingIn, setLoggingIn] = useState(false)
  const handleLogin = async (e) => {
    e.preventDefault()
    if (!passwordInput || loggingIn) return
    setLoggingIn(true)
    try {
      const response = await axios.post(`${API_BASE_URL}/login`, { password: passwordInput })
      writeToken(response.data.token)
      setIsAuthenticated(true)
      setAuthError('')
    } catch (err) {
      setAuthError(err.response?.data?.message || 'Cannot reach the server. Please try again.')
    } finally {
      setPasswordInput('')
      setLoggingIn(false)
    }
  }

  // Validate and migrate a log entry's schema
  const validateLogEntry = (log) => {
    if (!log || typeof log !== 'object') return null
    if (!log.id || !log.datetime) return null
    return {
      id: log.id,
      datetime: log.datetime,
      datetimeDisplay: log.datetimeDisplay || null,
      datetimeSource: log.datetimeSource || 'unknown',
      notes: log.notes || '',
      savedAt: log.savedAt || log.datetime,
      analysis: log.analysis || null,
      trainingPlan: log.trainingPlan || null,
      dbSessionId: log.dbSessionId || null,
      metrics: {
        skiIQ: log.metrics?.skiIQ || null,
        balance: log.metrics?.balance || null,
        edging: log.metrics?.edging || null,
        rotary: log.metrics?.rotary || null,
        performance: log.metrics?.performance || null
      }
    }
  }

  // Load progress logs from localStorage on mount
  useEffect(() => {
    const savedLogs = localStorage.getItem(STORAGE_KEY)
    if (savedLogs) {
      try {
        const parsed = JSON.parse(savedLogs)
        if (!Array.isArray(parsed)) {
          console.error('Progress logs is not an array, resetting')
          localStorage.removeItem(STORAGE_KEY)
          return
        }
        // Validate each entry, strip base64 previews from old entries
        const validated = parsed
          .map(log => {
            const valid = validateLogEntry(log)
            if (valid) {
              // Strip screenshotPreviews (base64 bloat) from old entries
              delete log.screenshotPreviews
            }
            return valid
          })
          .filter(Boolean)
        setProgressLogs(validated)
        // Re-save without bloat if we stripped anything
        if (validated.length !== parsed.length || savedLogs.includes('screenshotPreviews')) {
          try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(validated))
          } catch (e) {
            // Quota issue even after cleanup - clear and rely on DB
            console.error('localStorage quota exceeded even after cleanup')
          }
        }
      } catch (e) {
        console.error('Failed to load progress logs, resetting:', e)
        localStorage.removeItem(STORAGE_KEY)
      }
    }
  }, [])

  // Save progress logs to localStorage whenever they change (with quota handling)
  useEffect(() => {
    if (progressLogs.length > 0) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(progressLogs))
      } catch (e) {
        console.error('localStorage quota exceeded:', e)
        // Try saving without analysis data as a last resort
        try {
          const slim = progressLogs.map(log => ({
            ...log,
            analysis: null,
            trainingPlan: null
          }))
          localStorage.setItem(STORAGE_KEY, JSON.stringify(slim))
        } catch (e2) {
          console.error('Cannot save to localStorage at all:', e2)
        }
      }
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  }, [progressLogs])

  // Load sessions from database on mount and merge them into the progress log,
  // so a new device or cleared browser still shows every saved session
  const [dbLoadFailed, setDbLoadFailed] = useState(false)
  useEffect(() => {
    const loadDbSessions = async () => {
      try {
        const response = await axios.get(`${API_BASE_URL}/sessions`)
        const sessions = Array.isArray(response.data) ? response.data : []
        setDbSessions(sessions)
        setDbLoadFailed(false)
        setProgressLogs(prev => mergeDbSessionsIntoLogs(prev, sessions))
      } catch (err) {
        console.log('Could not load sessions from database (may not be configured yet)')
        setDbLoadFailed(true)
      }
    }
    if (isAuthenticated) loadDbSessions()
  }, [isAuthenticated])

  // Save session to database
  const saveSessionToDb = async (sessionData) => {
    setSavingToDb(true)
    try {
      const response = await axios.post(`${API_BASE_URL}/sessions`, sessionData)
      setDbSessions(prev => [response.data, ...prev])
      return response.data
    } catch (err) {
      console.error('Failed to save to database:', err)
      throw err
    } finally {
      setSavingToDb(false)
    }
  }

  // Delete session from database (throws on failure so callers can keep the local copy)
  const deleteSessionFromDb = async (sessionId) => {
    try {
      await axios.delete(`${API_BASE_URL}/sessions/${sessionId}`)
    } catch (err) {
      // Already gone from the database counts as deleted
      if (err.response?.status !== 404) {
        console.error('Failed to delete from database:', err)
        throw err
      }
    }
    setDbSessions(prev => prev.filter(s => s.id !== sessionId))
  }

  // Migration state
  const [migrating, setMigrating] = useState(false)
  const [migrationResult, setMigrationResult] = useState(null)

  // Migrate localStorage logs to database
  const migrateLogsToDatabase = async () => {
    const unsyncedLogs = progressLogs.filter(log => !log.dbSessionId)
    if (unsyncedLogs.length === 0) {
      setMigrationResult({ success: true, message: 'All sessions are already in the cloud' })
      return
    }

    setMigrating(true)
    setMigrationResult(null)
    let migrated = 0
    let skipped = 0
    let failed = 0

    // Re-read the database first: if it can't be reached we can't tell what is
    // already there, and uploading anyway would create duplicates
    let currentDbSessions
    try {
      const response = await axios.get(`${API_BASE_URL}/sessions`)
      currentDbSessions = Array.isArray(response.data) ? response.data : []
      setDbSessions(currentDbSessions)
      setDbLoadFailed(false)
    } catch (err) {
      setMigrating(false)
      setDbLoadFailed(true)
      setMigrationResult({ success: false, message: 'Cannot reach the cloud database right now, so nothing was uploaded. Please try again shortly.' })
      return
    }

    const linkedIds = {} // local log id -> database session id
    const claimed = new Set(progressLogs.map(log => log.dbSessionId).filter(Boolean))

    for (const log of unsyncedLogs) {
      // Already in the database? Link it instead of uploading again
      const existing = currentDbSessions.find(s => !claimed.has(s.id) && (s.id === log.id || isSameSessionMoment(s, log)))
      if (existing) {
        claimed.add(existing.id)
        linkedIds[log.id] = existing.id
        skipped++
        continue
      }

      try {
        const sessionData = {
          session_date: log.datetime,
          notes: log.notes || '',
          metrics: log.analysis || {
            session_overview: {
              ski_iq_range: { average: log.metrics?.skiIQ }
            },
            overall_metrics: {
              balance: log.metrics?.balance,
              edging: log.metrics?.edging,
              rotary: log.metrics?.rotary,
              performance: log.metrics?.performance
            }
          },
          training_plan: serializePlan(log.trainingPlan)
        }

        const response = await axios.post(`${API_BASE_URL}/sessions`, sessionData)
        setDbSessions(prev => [response.data, ...prev])
        claimed.add(response.data.id)
        linkedIds[log.id] = response.data.id
        migrated++
      } catch (err) {
        console.error('Failed to migrate log:', log.id, err)
        failed++
      }
    }

    // Remember which database session each local log belongs to, so later
    // syncs skip it and deleting it removes the database copy too
    setProgressLogs(prev => prev.map(log =>
      linkedIds[log.id] ? { ...log, dbSessionId: linkedIds[log.id] } : log
    ))

    setMigrating(false)
    setMigrationResult({
      success: true,
      message: `Migration complete: ${migrated} uploaded, ${skipped} already existed, ${failed} failed`
    })
  }

  // Analyze progression across sessions (all by default, or selected subset)
  const [progressionError, setProgressionError] = useState(null)
  const analyzeProgression = async (sessionIds = null) => {
    if (dbSessions.length < 2) {
      setError('Need at least 2 saved sessions to analyze progression')
      return
    }
    setLoadingProgression(true)
    setProgressionError(null)
    setProgressionAnalysis(null)
    setShowProgressionModal(true)
    try {
      const response = await axios.post(`${API_BASE_URL}/progression`, {
        session_ids: sessionIds || (selectedSessionIds.length > 0 ? selectedSessionIds : null)
      }, { timeout: 120000 }) // 2 min timeout - progression analysis uses Sonnet and can take time
      setProgressionAnalysis(response.data)
      // Update available sessions from response
      if (response.data.available_sessions) {
        setSelectedSessionIds(response.data.session_ids_analyzed || [])
      }
    } catch (err) {
      let message
      if (err.code === 'ERR_NETWORK' || !err.response) {
        message = 'Cannot reach the server. Check that the backend is running.'
      } else if (err.response?.status === 500) {
        message = err.response.data?.error || 'Server error during analysis. The AI service may be unavailable.'
      } else {
        message = err.response?.data?.message || err.response?.data?.error || err.message || 'Failed to analyze progression'
      }
      setProgressionError(message)
    } finally {
      setLoadingProgression(false)
    }
  }

  // Toggle session selection for progression analysis
  const toggleSessionSelection = (sessionId) => {
    setSelectedSessionIds(prev =>
      prev.includes(sessionId)
        ? prev.filter(id => id !== sessionId)
        : [...prev, sessionId]
    )
  }

  // Re-run analysis with new session selection
  const rerunProgressionAnalysis = () => {
    if (selectedSessionIds.length < 2) {
      setError('Select at least 2 sessions to analyze')
      return
    }
    analyzeProgression(selectedSessionIds)
  }

  // Extract metadata from files when selected
  const extractMetadataFromFiles = async (files) => {
    try {
      const formData = new FormData()
      files.forEach(fileObj => {
        formData.append('images', fileObj.file)
      })

      const response = await axios.post(`${API_BASE_URL}/extract-metadata`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })

      // Find the first datetime from metadata (EXIF/filename times are local wall-clock time)
      if (response.data.metadata) {
        for (const meta of response.data.metadata) {
          const formatted = normaliseLocalDateTime(meta.datetime)
          if (formatted) {
            setSessionDateTime(formatted)
            return
          }
        }
      }
      // If no datetime found, use current local time
      setSessionDateTime(toLocalInputValue())
    } catch (e) {
      console.error('Failed to extract metadata:', e)
      setSessionDateTime(toLocalInputValue())
    }
  }

  // Handle file selection from input (multiple files)
  const handleFileSelect = (event) => {
    const files = Array.from(event.target.files)
    processFiles(files)
  }

  // Handle drag and drop (multiple files)
  const handleDrop = (event) => {
    event.preventDefault()
    event.stopPropagation()
    const files = Array.from(event.dataTransfer.files)
    processFiles(files)
  }

  const handleDragOver = (event) => {
    event.preventDefault()
    event.stopPropagation()
  }

  const handleDragEnter = (event) => {
    event.preventDefault()
    event.stopPropagation()
  }

  // Process multiple files
  const processFiles = (files) => {
    const validTypes = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp']
    const validFiles = []
    const errors = []

    files.forEach(file => {
      if (!validTypes.includes(file.type)) {
        errors.push(`${file.name}: Invalid file type`)
        return
      }
      if (file.size > 5 * 1024 * 1024) {
        errors.push(`${file.name}: File too large (max 5MB)`)
        return
      }
      validFiles.push(file)
    })

    if (errors.length > 0) {
      setError(errors.join(', '))
    } else {
      setError(null)
    }

    // Create previews for valid files
    const newFileObjects = []
    validFiles.forEach(file => {
      const reader = new FileReader()
      reader.onloadend = () => {
        const newFile = {
          id: `${file.name}-${Date.now()}-${Math.random()}`,
          file: file,
          preview: reader.result,
          name: file.name
        }
        newFileObjects.push(newFile)
        setSelectedFiles(prev => [...prev, newFile])

        // After all files loaded, extract metadata
        if (newFileObjects.length === validFiles.length) {
          extractMetadataFromFiles(newFileObjects)
        }
      }
      reader.readAsDataURL(file)
    })

    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  // Remove a single file
  const removeFile = (id) => {
    setSelectedFiles(prev => prev.filter(f => f.id !== id))
  }

  // Analyze all screenshots holistically (async with polling to avoid timeouts)
  const analyzeAllScreenshots = async () => {
    if (selectedFiles.length === 0) {
      setError('Please select at least one screenshot')
      return
    }

    setLoading(true)
    setError(null)
    setAnalysis(null)
    setLoadedLogId(null)
    setEditingValues(false)
    setTrainingPlan(null)

    try {
      // Send all images to start async analysis
      const formData = new FormData()
      selectedFiles.forEach(fileObj => {
        formData.append('images', fileObj.file)
      })

      const submitResponse = await axios.post(`${API_BASE_URL}/analyze-async`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      })

      const jobId = submitResponse.data.job_id

      // Poll for results
      const pollInterval = 2000 // 2 seconds
      const maxPolls = 90 // 3 minutes max
      const maxConsecutiveFailures = 3 // ride out a brief server restart or network blip
      let polls = 0
      let consecutiveFailures = 0

      while (polls < maxPolls) {
        await new Promise(resolve => setTimeout(resolve, pollInterval))
        if (!isMountedRef.current) return
        polls++

        let statusResponse
        try {
          statusResponse = await axios.get(`${API_BASE_URL}/job-status/${jobId}`)
          consecutiveFailures = 0
        } catch (pollErr) {
          // The server no longer knows this job (e.g. it restarted): retrying won't help
          if (pollErr.response?.status === 404) {
            setError('The server lost track of this analysis (it may have restarted). Please analyze again.')
            return
          }
          consecutiveFailures++
          if (consecutiveFailures >= maxConsecutiveFailures) throw pollErr
          continue
        }
        if (!isMountedRef.current) return
        const { status, result, error: jobError } = statusResponse.data

        if (status === 'completed') {
          setAnalysis(result)

          // Update sessionDateTime from CARV screenshot if available (master timestamp)
          const carvDateTime = normaliseLocalDateTime(result?.session_overview?.session_datetime)
          if (carvDateTime) {
            setSessionDateTime(carvDateTime)
          }
          return
        }

        if (status === 'failed') {
          setError(jobError || 'Analysis failed')
          return
        }
        // status is 'pending' or 'processing' - continue polling
      }

      // Timeout after max polls
      setError('Analysis timed out. Please try again with fewer screenshots.')
    } catch (err) {
      const errorMessage = err.response?.data?.message ||
                          err.response?.data?.error ||
                          'Failed to analyze screenshots. Is the backend running?'
      setError(errorMessage)
    } finally {
      setLoading(false)
    }
  }

  // Generate training plan based on analysis
  const generateTrainingPlan = async () => {
    if (!analysis) {
      setError('Please analyze screenshots first')
      return
    }

    setPlanLoading(true)
    setError(null)

    try {
      // If this analysis is a saved session, don't count it as its own history
      const savedLog = progressLogs.find(log => log.analysis === analysis)
      const response = await axios.post(`${API_BASE_URL}/generate-plan`, {
        ...analysis,
        days_available: planDays,
        runs_per_day: planRuns,
        session_id: savedLog?.dbSessionId || null
      })
      setTrainingPlan(parseStoredPlan(response.data.training_plan))
    } catch (err) {
      const errorMessage = err.response?.data?.message ||
                          err.response?.data?.error ||
                          'Failed to generate training plan'
      setError(errorMessage)
    } finally {
      setPlanLoading(false)
    }
  }

  // Export analysis results to text file
  const exportAnalysisToFile = () => {
    if (!analysis) {
      setError('No analysis data to export')
      return
    }

    const lines = []
    const divider = '═'.repeat(60)
    const subDivider = '─'.repeat(40)

    // Header
    lines.push(divider)
    lines.push('CARV SKIING ANALYSIS REPORT')
    lines.push(divider)
    lines.push('')

    // Session Overview
    if (analysis.session_overview) {
      lines.push('SESSION OVERVIEW')
      lines.push(subDivider)
      if (analysis.session_overview.session_date_display) {
        lines.push(`Date: ${analysis.session_overview.session_date_display}`)
      }
      if (analysis.session_overview.ski_iq_range) {
        const range = analysis.session_overview.ski_iq_range
        lines.push(`Ski:IQ Range: ${range.lowest} - ${range.highest} (Average: ${range.average})`)
      }
      if (analysis.session_overview.total_turns_analyzed) {
        lines.push(`Total Turns Analyzed: ${analysis.session_overview.total_turns_analyzed}`)
      }
      if (analysis.session_overview.terrain_types_seen?.length > 0) {
        lines.push(`Terrain Types: ${analysis.session_overview.terrain_types_seen.join(', ')}`)
      }
      lines.push(`Screenshots Analyzed: ${analysis.num_screenshots || 'N/A'}`)
      lines.push('')
    }

    // Holistic Analysis
    if (analysis.holistic_analysis) {
      lines.push('SKIING PROFILE')
      lines.push(subDivider)
      if (analysis.holistic_analysis.skiing_style) {
        lines.push(`Skiing Style: ${analysis.holistic_analysis.skiing_style}`)
      }
      if (analysis.holistic_analysis.technique_signature) {
        lines.push(`Technique Signature: ${analysis.holistic_analysis.technique_signature}`)
      }
      if (analysis.holistic_analysis.consistency_assessment) {
        lines.push(`Consistency: ${analysis.holistic_analysis.consistency_assessment}`)
      }
      if (analysis.holistic_analysis.biggest_limiter) {
        lines.push(`Biggest Limiter: ${analysis.holistic_analysis.biggest_limiter}`)
      }
      if (analysis.holistic_analysis.hidden_strength) {
        lines.push(`Hidden Strength: ${analysis.holistic_analysis.hidden_strength}`)
      }
      lines.push('')
    }

    // Detailed Observations
    if (analysis.detailed_observations) {
      lines.push('DETAILED ANALYSIS')
      lines.push(subDivider)
      lines.push(analysis.detailed_observations)
      lines.push('')
    }

    // Carv metrics and run details (current analyses)
    const exportVersion = getSkiIQVersion(analysis, sessionDateTime)
    if (exportVersion) {
      lines.push(`Ski:IQ scoring: ${exportVersion === 'stelvio' ? 'Ski:IQ 26 (Stelvio)' : 'Pre-26 (Wasatch)'}`)
      lines.push('')
    }
    const details = analysis.run_details
    if (details) {
      lines.push('RUN DETAILS')
      lines.push(subDivider)
      const detailLines = [
        ['Run', details.run_name], ['Based on', details.basis], ['Technique', details.technique],
        ['Snow surface', details.snow_surface], ['Pitch', details.pitch_deg != null ? `${details.pitch_deg}°` : null],
        ['Carving turns', details.carving_turns?.count != null ? `${details.carving_turns.count}/${details.carving_turns.of ?? '?'}` : null],
        ['Tempo', details.tempo_tpm != null ? `${details.tempo_tpm} turns/min` : null],
        ['Corridor', details.corridor_m != null ? `${details.corridor_m} m` : null],
        ['Radius', details.radius_m != null ? `${details.radius_m} m` : null],
        ['Balance', details.balance_rating]
      ]
      detailLines.forEach(([label, value]) => { if (value) lines.push(`${label}: ${value}`) })
      ;(details.skill_highlights || []).forEach(skill => {
        if (skill?.name) lines.push(`Carv skill${skill.number ? ` ${skill.number}` : ''}: ${skill.name} - ${skill.status || '?'}`)
      })
      lines.push('')
    }
    if (analysis.carv_metrics) {
      lines.push('CARV METRICS')
      lines.push(subDivider)
      CARV_METRIC_DEFS.forEach(def => {
        const entry = analysis.carv_metrics[def.key]
        if (entry?.value == null) return
        const zone = entry.zone ? ` (${ZONE_LABELS[entry.zone].toLowerCase()})` : ''
        lines.push(`${def.label}: ${formatMetricValue(entry.value, def.unit)}${zone}`)
      })
      lines.push('')
    }

    // Overall Metrics (older analyses without the Carv metrics list)
    if (analysis.overall_metrics && !analysis.carv_metrics) {
      lines.push('OVERALL METRICS')
      lines.push(subDivider)

      const formatMetricCategory = (name, metrics) => {
        if (!metrics) return
        lines.push(`\n${name}:`)
        Object.entries(metrics).forEach(([key, value]) => {
          if (value !== null && value !== undefined) {
            const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
            lines.push(`  ${label}: ${typeof value === 'number' ? value.toFixed(1) : value}`)
          }
        })
      }

      formatMetricCategory('Balance', analysis.overall_metrics.balance)
      formatMetricCategory('Edging', analysis.overall_metrics.edging)
      formatMetricCategory('Rotary', analysis.overall_metrics.rotary)
      formatMetricCategory('Performance', analysis.overall_metrics.performance)
      lines.push('')
    }

    // Top 3 Strengths
    if (analysis.top_3_strengths?.length > 0) {
      lines.push('TOP 3 STRENGTHS')
      lines.push(subDivider)
      analysis.top_3_strengths.forEach((strength, idx) => {
        const score = strength.score != null ? ` (${strength.score})` : ''
        lines.push(`${idx + 1}. ${strength.area || strength.metric || 'Strength'}${score}`)
        const why = strength.why_it_matters || strength.observation
        if (why) lines.push(`   ${why}`)
        if (strength.impact) lines.push(`   Impact: ${strength.impact}`)
      })
      lines.push('')
    }

    // Top 3 Priorities
    if (analysis.top_3_priorities?.length > 0) {
      lines.push('TOP 3 PRIORITIES FOR IMPROVEMENT')
      lines.push(subDivider)
      analysis.top_3_priorities.forEach((priority, idx) => {
        lines.push(`${idx + 1}. ${priority.area || priority.metric || 'Priority'}`)
        if (priority.current_score != null || priority.target_score != null) {
          lines.push(`   Score: ${priority.current_score ?? '?'} → target ${priority.target_score ?? '?'}`)
        }
        if (priority.why_priority) lines.push(`   Why: ${priority.why_priority}`)
        if (priority.quick_win) lines.push(`   Quick Win: ${priority.quick_win}`)
        if (priority.current_issue) lines.push(`   Current Issue: ${priority.current_issue}`)
        if (priority.root_cause) lines.push(`   Root Cause: ${priority.root_cause}`)
        if (priority.specific_drill) lines.push(`   Drill: ${priority.specific_drill}`)
        if (priority.success_metric) lines.push(`   Success Metric: ${priority.success_metric}`)
      })
      lines.push('')
    }

    // Run-by-Run Notes
    if (analysis.run_by_run_notes?.length > 0) {
      lines.push('RUN-BY-RUN NOTES')
      lines.push(subDivider)
      analysis.run_by_run_notes.forEach((note, idx) => {
        lines.push(`Screenshot ${note.screenshot || idx + 1}:`)
        if (note.key_observation) lines.push(`  ${note.key_observation}`)
        if (note.ski_iq) lines.push(`  Ski:IQ: ${note.ski_iq}`)
        if (note.terrain) lines.push(`  Terrain: ${note.terrain}`)
        if (note.highlight) lines.push(`  Highlight: ${note.highlight}`)
        if (note.focus_area) lines.push(`  Focus Area: ${note.focus_area}`)
        lines.push('')
      })
    }

    // Training Plan (if available)
    if (trainingPlan) {
      lines.push(divider)
      lines.push('TRAINING PLAN')
      lines.push(divider)
      lines.push(planToText(parseStoredPlan(trainingPlan)))
      lines.push('')
    }

    // Footer
    lines.push(divider)
    lines.push(`Exported: ${new Date().toLocaleString()}`)
    lines.push('Generated by CARV Trainer AI')
    lines.push(divider)

    // Create and download file
    const content = lines.join('\n')
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url

    // Generate filename with the session's local date
    const dateStr = (normaliseLocalDateTime(analysis.session_overview?.session_datetime) || toLocalInputValue()).slice(0, 10)
    link.download = `carv-analysis-${dateStr}.txt`

    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  // Send chat message
  const sendChatMessage = async () => {
    if (!chatInput.trim() || chatLoading) return

    const userMessage = chatInput.trim()
    setChatInput('')
    setChatLoading(true)

    // Add user message to chat
    const newMessages = [...chatMessages, { role: 'user', content: userMessage }]
    setChatMessages(newMessages)

    try {
      // Build session history summary for chat context
      const sessionHistory = progressLogs.length > 0 ? progressLogs.map(log => ({
        date: log.datetime,
        skiIQ: log.metrics?.skiIQ,
        balance: log.metrics?.balance?.category_average,
        edging: log.metrics?.edging?.category_average,
        rotary: log.metrics?.rotary?.category_average,
        twr: getLogMetricValue(log, CARV_METRIC_DEFS.find(d => d.key === 'weight_release')),
        edgeAngle: getLogMetricValue(log, CARV_METRIC_DEFS.find(d => d.key === 'edge_angle')),
        skiIQVersion: getSkiIQVersion(log.analysis, log.datetime),
        technique: log.analysis?.run_details?.technique || null,
        snowSurface: log.analysis?.run_details?.snow_surface || null,
        pitch: log.analysis?.run_details?.pitch_deg ?? log.analysis?.carv_metrics?.slope_pitch?.value ?? null,
        notes: log.notes
      })) : null

      const response = await axios.post(`${API_BASE_URL}/chat`, {
        message: userMessage,
        history: chatMessages,
        analysisData: analysis,
        sessionHistory: sessionHistory,
        progressionInsights: progressionAnalysis?.summary || progressionAnalysis?.technique_narrative || null
      })

      // Add assistant response
      setChatMessages([...newMessages, { role: 'assistant', content: response.data.response }])
    } catch (err) {
      const errorMessage = err.response?.data?.message || 'Failed to send message'
      setChatMessages([...newMessages, { role: 'assistant', content: `Sorry, I encountered an error: ${errorMessage}` }])
    } finally {
      setChatLoading(false)
    }
  }

  // Scroll chat to bottom when messages change
  useEffect(() => {
    if (chatMessagesEndRef.current) {
      chatMessagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [chatMessages])

  // Get color class based on score
  const getScoreColor = (score) => {
    if (score === null || score === undefined) return 'score-unknown'
    if (score >= 70) return 'score-good'
    if (score >= 50) return 'score-medium'
    return 'score-poor'
  }

  // Format metric name for display
  const formatMetricName = (name) => {
    return name
      .split('_')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  }

  // Render a metric card
  const renderMetricCard = (category, metrics, icon) => {
    if (!metrics) return null

    const metricEntries = Object.entries(metrics).filter(([key, value]) =>
      value !== null && key !== 'category_average'
    )
    if (metricEntries.length === 0) return null

    const categoryAvg = metrics.category_average

    return (
      <div className="metric-card">
        <div className="metric-card-header">
          <span className="metric-icon">{icon}</span>
          <h3>{category}</h3>
          {categoryAvg !== null && categoryAvg !== undefined && (
            <span className={`category-average ${getScoreColor(categoryAvg)}`}>
              {Math.round(categoryAvg)}
            </span>
          )}
        </div>
        <div className="metric-list">
          {metricEntries.map(([key, value]) => (
            <div key={key} className="metric-item">
              <span className="metric-name">{formatMetricName(key)}</span>
              <span className={`metric-value ${getScoreColor(value)}`}>
                {value !== null ? Math.round(value) : 'N/A'}
              </span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  // Carv's Ski:IQ Metrics list with units and performance-zone position
  const renderCarvMetrics = (carvMetrics) => {
    const rows = CARV_METRIC_DEFS.filter(def => carvMetrics?.[def.key]?.value != null)
    if (rows.length === 0) return null
    return (
      <div className="carv-metrics-list">
        {rows.map(def => {
          const { value, zone } = carvMetrics[def.key]
          const showBar = def.unit === '%' && !def.zoneOnly
          return (
            <div key={def.key} className="carv-metric-row">
              <div className="carv-metric-top">
                <span className="carv-metric-label">{def.label}</span>
                <span className="carv-metric-value">{formatMetricValue(value, def.unit)}</span>
              </div>
              <div className="carv-metric-bottom">
                {showBar ? (
                  <div className="carv-metric-bar">
                    <div className="carv-metric-bar-fill" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
                  </div>
                ) : (
                  <span className="carv-metric-note">
                    {def.zoneOnly ? 'Judged against its zone' : ''}
                  </span>
                )}
                {zone && <span className={`zone-chip zone-${zone}`}>{ZONE_LABELS[zone]}</span>}
              </div>
            </div>
          )
        })}
      </div>
    )
  }

  // Run details Carv shows around the metrics: technique, snow, rhythm, Carv's own skill verdicts
  const renderRunDetails = (details) => {
    if (!details) return null
    const capitalise = (text) => text ? text.charAt(0).toUpperCase() + text.slice(1) : text
    const items = [
      ['Technique', capitalise(details.technique)],
      ['Snow', capitalise(details.snow_surface)],
      ['Pitch', details.pitch_deg != null ? `${details.pitch_deg}°` : null],
      ['Carving turns', details.carving_turns?.count != null ? `${details.carving_turns.count}/${details.carving_turns.of ?? '?'}` : null],
      ['Tempo', details.tempo_tpm != null ? `${details.tempo_tpm} tpm` : null],
      ['Corridor', details.corridor_m != null ? `${details.corridor_m} m` : null],
      ['Radius', details.radius_m != null ? `${details.radius_m} m` : null],
      ['Balance', details.balance_rating],
      ['Segment', [details.segment?.time, details.segment?.turns != null ? `${details.segment.turns} turns` : null, details.segment?.vert, details.segment?.distance].filter(Boolean).join(' · ') || null]
    ].filter(([, value]) => value)
    const skills = (details.skill_highlights || []).filter(s => s?.name)
    if (items.length === 0 && skills.length === 0) return null
    return (
      <div className="run-details">
        {details.run_name && <div className="run-details-name">{details.run_name}{details.basis ? ` · ${details.basis}` : ''}</div>}
        {items.length > 0 && (
          <div className="run-details-grid">
            {items.map(([label, value]) => (
              <div key={label} className="run-detail">
                <span className="run-detail-label">{label}</span>
                <span className="run-detail-value">{value}</span>
              </div>
            ))}
          </div>
        )}
        {skills.length > 0 && (
          <div className="skill-highlights">
            <span className="run-detail-label">Carv skill highlights</span>
            {skills.map((skill, idx) => (
              <div key={idx} className="skill-highlight">
                <span>{skill.number ? `Skill ${skill.number}: ` : ''}{skill.name}</span>
                <span className={`skill-status ${/work on/i.test(skill.status || '') ? 'work-on' : 'nailed'}`}>{skill.status}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // ---- Correcting values the AI misread. Corrected fields are listed in
  // analysis.user_corrected so later AI calls treat them as ground truth.
  const markCorrected = (a, path) => ({ ...a, user_corrected: Array.from(new Set([...(a.user_corrected || []), path])) })

  const updateCarvMetric = (key, field, raw) => setAnalysis(prev => {
    if (!prev) return prev
    const def = CARV_METRIC_DEFS.find(d => d.key === key)
    const current = prev.carv_metrics?.[key] || { value: null, unit: def.unit, zone: null }
    let value = current.value
    if (field === 'value') {
      value = raw === '' ? null : Number(raw)
      if (value !== null && Number.isNaN(value)) return prev
    }
    const zone = field === 'zone' ? (raw || null) : current.zone
    const carv = { ...(prev.carv_metrics || {}) }
    if (value === null) delete carv[key]
    else carv[key] = { value, unit: def.unit, zone }
    // Keep the older grouped fields in step (the server recomputes averages when saved)
    let overall = prev.overall_metrics
    if (def.legacy) {
      const [category, legacyKey] = def.legacy
      overall = { ...(overall || {}), [category]: { ...(overall?.[category] || {}), [legacyKey]: value } }
    }
    return markCorrected({ ...prev, carv_metrics: carv, overall_metrics: overall }, `carv_metrics.${key}`)
  })

  const updateSkiIQValue = (raw) => setAnalysis(prev => {
    if (!prev) return prev
    const value = raw === '' ? null : Number(raw)
    if (value !== null && Number.isNaN(value)) return prev
    return markCorrected({
      ...prev,
      session_overview: { ...(prev.session_overview || {}), ski_iq_range: { lowest: value, highest: value, average: value } }
    }, 'ski_iq')
  })

  const updateRunDetail = (key, raw, numeric = false) => setAnalysis(prev => {
    if (!prev) return prev
    let value = raw === '' ? null : raw
    if (numeric && value !== null) {
      value = Number(value)
      if (Number.isNaN(value)) return prev
    }
    return markCorrected({ ...prev, run_details: { ...(prev.run_details || {}), [key]: value } }, `run_details.${key}`)
  })

  const updateSessionDateValue = (raw) => {
    setSessionDateTime(raw)
    setAnalysis(prev => prev ? markCorrected({
      ...prev,
      session_overview: { ...(prev.session_overview || {}), session_datetime: raw ? `${raw}:00` : null, session_date_display: null }
    }, 'session_datetime') : prev)
  }

  // Inputs for every value Carv shows, so misread or missing values can be fixed
  const renderValueEditor = () => {
    const details = analysis?.run_details || {}
    const range = analysis?.session_overview?.ski_iq_range || {}
    return (
      <section className="metrics-section value-editor">
        <h2>Correct the values</h2>
        <p className="value-editor-note">Fix anything the AI misread from your screenshots. Corrected values are marked so coaching treats them as right. Save the session afterwards to keep them.</p>
        <div className="value-editor-grid">
          <label>Ski:IQ
            <input type="number" inputMode="numeric" value={range.average ?? ''} onChange={(e) => updateSkiIQValue(e.target.value)} />
          </label>
          <label>Date and time
            <input type="datetime-local" value={normaliseLocalDateTime(analysis?.session_overview?.session_datetime) || sessionDateTime || ''} onChange={(e) => updateSessionDateValue(e.target.value)} />
          </label>
          <label>Technique
            <select value={details.technique || ''} onChange={(e) => updateRunDetail('technique', e.target.value)}>
              <option value="">Unknown</option>
              <option value="carving">Carving</option>
              <option value="short turns">Short turns</option>
              <option value="parallel">Parallel</option>
            </select>
          </label>
          <label>Snow surface
            <input type="text" value={details.snow_surface || ''} placeholder="e.g. Smooth" onChange={(e) => updateRunDetail('snow_surface', e.target.value)} />
          </label>
          <label>Pitch (°)
            <input type="number" inputMode="decimal" value={details.pitch_deg ?? ''} onChange={(e) => updateRunDetail('pitch_deg', e.target.value, true)} />
          </label>
          <label>Balance
            <select value={details.balance_rating || ''} onChange={(e) => updateRunDetail('balance_rating', e.target.value)}>
              <option value="">Unknown</option>
              <option value="Ok">Ok</option>
              <option value="Good">Good</option>
              <option value="Great">Great</option>
            </select>
          </label>
        </div>
        <div className="value-editor-metrics">
          {CARV_METRIC_DEFS.map(def => {
            const entry = analysis?.carv_metrics?.[def.key]
            return (
              <div key={def.key} className="value-editor-row">
                <span className="carv-metric-label">{def.label} ({def.unit})</span>
                <input type="number" inputMode="decimal" step={def.unit === 'g' ? '0.1' : '1'} value={entry?.value ?? ''}
                  onChange={(e) => updateCarvMetric(def.key, 'value', e.target.value)} aria-label={`${def.label} value`} />
                <select value={entry?.zone || ''} onChange={(e) => updateCarvMetric(def.key, 'zone', e.target.value)} aria-label={`${def.label} zone`}>
                  <option value="">Zone ?</option>
                  <option value="below">Below zone</option>
                  <option value="in">In zone</option>
                  <option value="above">Above zone</option>
                </select>
              </div>
            )
          })}
        </div>
      </section>
    )
  }

  // Set the Ski:IQ scoring version on the current analysis (Carv rescored with Ski:IQ 26)
  const setAnalysisSkiIQVersion = (version) => {
    setAnalysis(prev => prev ? {
      ...prev,
      session_overview: { ...(prev.session_overview || {}), ski_iq_version: version }
    } : prev)
  }

  // Extract search query from YouTube URL and build embed URL
  const getYouTubeSearchQuery = (url) => {
    if (!url) return null
    try {
      const urlObj = new URL(url)
      return urlObj.searchParams.get('search_query') || urlObj.searchParams.get('q') || null
    } catch {
      return null
    }
  }

  // Light markdown for chat replies and older text plans: headings, bullets, **bold**
  const renderInlineBold = (line) => line.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part
  )
  const renderTextLines = (text, keyPrefix = '') => text.split('\n').map((line, i) => {
    const key = `${keyPrefix}${i}`
    const trimmed = line.trim()
    if (trimmed === '') return <br key={key} />
    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/)
    if (heading) return <p key={key} className={`md-heading md-h${heading[1].length}`}><strong>{heading[2].replace(/\*\*/g, '')}</strong></p>
    const bullet = trimmed.match(/^[-*]\s+(.*)$/)
    if (bullet) return <p key={key} style={{ paddingLeft: '12px' }}>{'• '}{renderInlineBold(bullet[1])}</p>
    return <p key={key}>{renderInlineBold(line)}</p>
  })

  // Render chat/text content with embedded YouTube videos
  const renderContentWithVideos = (text) => {
    if (!text) return null
    // Split by YouTube URLs (both search and watch URLs)
    const urlRegex = /(https?:\/\/(?:www\.)?youtube\.com\/(?:results\?search_query=[^\s)]+|watch\?v=[^\s)]+))/g
    const mdLinkRegex = /\[([^\]]+)\]\((https?:\/\/(?:www\.)?youtube\.com\/(?:results\?search_query=[^\s)]+|watch\?v=[^\s)]+))\)/g

    // First, replace markdown links with a placeholder
    let processed = text
    const videoLinks = []
    let match

    // Collect all YouTube markdown links
    while ((match = mdLinkRegex.exec(text)) !== null) {
      videoLinks.push({ label: match[1], url: match[2], fullMatch: match[0] })
    }

    // Collect bare YouTube URLs not in markdown format
    const bareUrlRegex = /(?<!\()(?<!\])(https?:\/\/(?:www\.)?youtube\.com\/(?:results\?search_query=[^\s)]+|watch\?v=[^\s)]+))/g
    while ((match = bareUrlRegex.exec(text)) !== null) {
      // Check it's not already part of a markdown link
      if (!videoLinks.some(v => v.url === match[1])) {
        videoLinks.push({ label: 'Watch Video', url: match[1], fullMatch: match[1] })
      }
    }

    if (videoLinks.length === 0) {
      // No YouTube links, render as formatted text
      return renderTextLines(text)
    }

    // Split the text and insert video embeds
    const parts = []
    let lastIndex = 0
    const allMatches = [...text.matchAll(/\[([^\]]*)\]\((https?:\/\/(?:www\.)?youtube\.com\/(?:results\?search_query=[^\s)]+|watch\?v=[^\s)]+))\)|(https?:\/\/(?:www\.)?youtube\.com\/(?:results\?search_query=[^\s)]+|watch\?v=[^\s)]+))/g)]

    allMatches.forEach((m, i) => {
      const before = text.slice(lastIndex, m.index)
      if (before) {
        parts.push(<div key={`t${i}`}>{renderTextLines(before.replace(/\[$/, ''), `t${i}-`)}</div>)
      }
      const url = m[2] || m[3]
      const query = getYouTubeSearchQuery(url)
      const label = m[1] || query?.replace(/\+/g, ' ') || 'Watch Video'
      parts.push(
        <a key={`v${i}`} href={url} target="_blank" rel="noopener noreferrer" className="chat-video-card">
          <div className="chat-video-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none">
              <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
            </svg>
          </div>
          <div className="chat-video-info">
            <span className="chat-video-title">{label}</span>
            <span className="chat-video-subtitle">Watch on YouTube</span>
          </div>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="chat-video-arrow">
            <polyline points="9 18 15 12 9 6"/>
          </svg>
        </a>
      )
      lastIndex = m.index + m[0].length
    })

    const remaining = text.slice(lastIndex)
    if (remaining) {
      parts.push(<div key="end">{renderTextLines(remaining, 'end-')}</div>)
    }

    return <>{parts}</>
  }

  // Render a training plan: structured plans as sections, older markdown plans as formatted text
  const renderTrainingPlan = (storedPlan) => {
    const plan = parseStoredPlan(storedPlan)
    if (!plan) return null
    if (!isStructuredPlan(plan)) {
      return <div className="training-plan-raw">{renderContentWithVideos(plan)}</div>
    }

    const icon = (children) => (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">{children}</svg>
    )
    const dailySchedule = buildDailySchedule(plan)
    const weeklySchedule = buildWeeklySchedule(plan)
    const focusDrill = plan.immediate_focus?.reference
    const cues = [
      ['Primary', plan.mental_cues?.primary],
      ['Transition', plan.mental_cues?.transition],
      ['Confidence', plan.mental_cues?.confidence]
    ].filter(([, cue]) => cue)

    return (
      <div className="training-plan-structured">
        <div className="plan-header">
          <div className="plan-header-icon">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/>
              <line x1="16" y1="17" x2="8" y2="17"/>
            </svg>
          </div>
          <h1>Training Plan{plan.ski_iq ? ` for Ski:IQ ${Math.round(plan.ski_iq)}` : ''}</h1>
        </div>

        {/* The Big Picture */}
        <div className="plan-section big-picture-section">
          <div className="section-header">
            <span className="section-icon">{icon(<><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></>)}</span>
            <h2>The Big Picture</h2>
            {plan.level && <span className="section-badge">{plan.level}</span>}
          </div>
          <div className="big-picture-content">
            {plan.summary && <p>{plan.summary}</p>}
            {plan.biggest_limiter && <p><strong>Biggest limiter:</strong> {plan.biggest_limiter}</p>}
            {plan.carv_alignment && <p><strong>Carv's view:</strong> {plan.carv_alignment}</p>}
            {plan.changes_since_last && <p><strong>Since last time:</strong> {plan.changes_since_last}</p>}
          </div>
        </div>

        {/* Immediate Focus */}
        {plan.immediate_focus?.issue && (
          <div className="plan-section immediate-focus-section">
            <div className="section-header">
              <span className="section-icon focus-icon">{icon(<><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></>)}</span>
              <h2>Immediate Focus</h2>
              <span className="section-badge">Next 1-3 Runs</span>
            </div>
            <div className="focus-card">
              {plan.immediate_focus.cue && (
                <div className="mental-cue-hero">
                  <span className="cue-label">Your Mantra</span>
                  <span className="cue-text">"{plan.immediate_focus.cue}"</span>
                </div>
              )}
              <ul className="focus-details">
                <li>{plan.immediate_focus.issue}</li>
                {focusDrill && <li>Drill: {focusDrill.name}{focusDrill.execution ? ` - ${focusDrill.execution}` : ''}</li>}
                {plan.immediate_focus.what_success_feels_like && <li>Success feels like: {plan.immediate_focus.what_success_feels_like}</li>}
              </ul>
            </div>
          </div>
        )}

        {/* 3 Key Drills */}
        <div className="plan-section drills-section">
          <div className="section-header">
            <span className="section-icon drills-icon">{icon(<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>)}</span>
            <h2>Your 3 Key Drills</h2>
            <span className="section-badge drill-badge">Master These</span>
          </div>
          <div className="drills-grid">
            {plan.drills.map((drill, idx) => (
              <div key={drill.drill_id + idx} className={`drill-card drill-${idx + 1}`}>
                <div className="drill-header">
                  <span className="drill-number">{idx + 1}</span>
                  <div className="drill-title">
                    <h3>{drillName(drill)}</h3>
                    {drill.role && <span className="drill-focus-tag">{drill.role.charAt(0).toUpperCase() + drill.role.slice(1)}</span>}
                  </div>
                </div>
                {drill.target_metric && (
                  <div className="drill-metric">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
                    <span>Target: {drill.target_metric}</span>
                  </div>
                )}
                {drill.why && <p className="drill-why">{drill.why}</p>}
                {drill.steps?.length > 0 && (
                  <div className="drill-execution">
                    <strong>How to do it:</strong>
                    <ol className="drill-steps">
                      {drill.steps.map((step, sIdx) => <li key={sIdx}>{step}</li>)}
                    </ol>
                  </div>
                )}
                <div className="drill-stats">
                  <div className="drill-stat">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>
                    <span>{drill.runs} run{drill.runs === 1 ? '' : 's'} a day</span>
                  </div>
                  <div className="drill-stat">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                    <span>{drill.focused_turns} focused turns per run</span>
                  </div>
                  {(drill.reference?.terrain || drill.reference?.duration) && (
                    <div className="drill-stat terrain">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 17l6-6 4 4 8-8"/></svg>
                      <span>{drill.reference.terrain || drill.reference.duration}</span>
                    </div>
                  )}
                </div>
                {drill.success_feels_like && (
                  <div className="drill-success">
                    <span className="success-label">Success feels like:</span>
                    <span className="success-text">"{drill.success_feels_like}"</span>
                  </div>
                )}
                {drill.mental_cue && (
                  <div className="drill-success">
                    <span className="success-label">Cue:</span>
                    <span className="success-text">"{drill.mental_cue}"</span>
                  </div>
                )}
                {(drill.common_mistakes || []).map((m, mIdx) => (
                  <div key={mIdx} className="drill-warning">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                    <span>Watch out: {m.mistake}{m.fix ? `. Fix: ${m.fix}` : ''}</span>
                  </div>
                ))}
                {drill.make_it_harder && <p className="drill-why"><strong>Make it harder:</strong> {drill.make_it_harder}</p>}
                {drill.reference?.video_url && (
                  <div className="drill-video-section">
                    <a href={drill.reference.video_url} target="_blank" rel="noopener noreferrer" className="drill-video-btn">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                        <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
                      </svg>
                      Watch Examples
                    </a>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Daily Session Plan, built from the three drills */}
        <div className="plan-section daily-plan-section">
          <div className="section-header">
            <span className="section-icon">{icon(<><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></>)}</span>
            <h2>Daily Session Plan</h2>
            <span className="section-badge">{plan.runs_per_day || 10} Runs</span>
          </div>
          <div className="daily-timeline">
            {dailySchedule.map((phase, idx) => (
              <div key={idx} className={`daily-phase phase-${idx + 1}`}>
                <div className="phase-marker">
                  <div className="phase-dot"></div>
                  {idx < dailySchedule.length - 1 && <div className="phase-line"></div>}
                </div>
                <div className="phase-content">
                  <div className="phase-header">
                    <span className="phase-runs">Run {phase.runs}</span>
                    <span className="phase-name">{phase.phase}</span>
                  </div>
                  <ul className="phase-details">
                    {phase.details.map((detail, dIdx) => <li key={dIdx}>{detail}</li>)}
                  </ul>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Weekly Training Schedule, sized to the days available */}
        <div className="plan-section weekly-schedule-section">
          <div className="section-header">
            <span className="section-icon">{icon(<><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></>)}</span>
            <h2>Weekly Training Schedule</h2>
            <span className="section-badge">{weeklySchedule.length} Day{weeklySchedule.length === 1 ? '' : 's'}</span>
          </div>
          <div className="weekly-grid">
            {weeklySchedule.map((day, idx) => (
              <div key={idx} className={`day-card day-${idx + 1}`}>
                <div className="day-header">
                  <span className="day-number">Day {day.dayNum}</span>
                  <span className="day-name">{day.name}</span>
                </div>
                <div className="day-content">
                  <div className="day-focus primary"><span className="focus-label">Primary:</span><span className="focus-value">{day.primaryFocus}</span></div>
                  <div className="day-focus secondary"><span className="focus-label">Then:</span><span className="focus-value">{day.secondary}</span></div>
                  <div className="day-terrain">{day.terrain}</div>
                  <div className="day-goal"><span className="goal-label">Goal:</span><span className="goal-value">{day.goal}</span></div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Priorities */}
        {plan.priorities?.length > 0 && (
          <div className="plan-section week-plan-section">
            <div className="section-header">
              <span className="section-icon">{icon(<><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></>)}</span>
              <h2>This Week's Priorities</h2>
            </div>
            <div className="priorities-cards">
              {plan.priorities.map((priority, idx) => (
                <div key={idx} className={`priority-card priority-${idx + 1}`}>
                  <div className="priority-badge">Priority {idx + 1}</div>
                  <h3>{priority.metric}</h3>
                  <div className="score-progression">
                    <span className="current-score">{priority.current}</span>
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
                    <span className="target-score">{priority.target}</span>
                  </div>
                  {priority.drill_name && (
                    <div className="drill-tag">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                      {priority.drill_name}
                    </div>
                  )}
                  {priority.note && <p className="drill-why">{priority.note}</p>}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Building on strengths */}
        {plan.strengths?.length > 0 && (
          <div className="plan-section checkpoints-section">
            <div className="section-header">
              <span className="section-icon">{icon(<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>)}</span>
              <h2>Building on Strengths</h2>
            </div>
            <div className="checkpoints-list">
              {plan.strengths.map((s, idx) => (
                <div key={idx} className="checkpoint-item"><span><strong>{s.strength}:</strong> {s.how_to_use}</span></div>
              ))}
            </div>
          </div>
        )}

        {/* 4-Week Progression */}
        {(plan.progression?.weeks_1_2?.length > 0 || plan.progression?.weeks_3_4?.length > 0) && (
          <div className="plan-section progression-section">
            <div className="section-header">
              <span className="section-icon">{icon(<><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></>)}</span>
              <h2>4-Week Progression</h2>
            </div>
            <div className="progression-grid">
              {[['1-2', 'Foundation', plan.progression.weeks_1_2, ''], ['3-4', 'Integration', plan.progression.weeks_3_4, ' phase-advanced']].map(([weeks, title, items, extra]) => items?.length > 0 && (
                <div key={weeks} className={`progression-phase${extra}`}>
                  <div className="phase-header"><span className="phase-icon">{weeks}</span><h3>{title}</h3></div>
                  <ul>{items.map((item, idx) => <li key={idx}>{item}</li>)}</ul>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Progress Checkpoints */}
        {plan.checkpoints?.length > 0 && (
          <div className="plan-section checkpoints-section">
            <div className="section-header">
              <span className="section-icon">{icon(<><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></>)}</span>
              <h2>Progress Checkpoints</h2>
            </div>
            <div className="checkpoints-list">
              {plan.checkpoints.map((checkpoint, idx) => (
                <div key={idx} className="checkpoint-item">
                  <div className="checkpoint-icon">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
                  </div>
                  <span><strong>{checkpoint.when}:</strong> {checkpoint.expect}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Mental Cues */}
        {cues.length > 0 && (
          <div className="plan-section cues-section">
            <div className="section-header">
              <span className="section-icon">{icon(<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>)}</span>
              <h2>Mental Cues</h2>
            </div>
            <div className="cues-grid">
              {cues.map(([label, cue]) => (
                <div key={label} className="cue-card">
                  <span className="cue-quote">"</span>
                  <p>{cue}</p>
                  <small>{label}</small>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Traps to Avoid */}
        {plan.traps?.length > 0 && (
          <div className="plan-section traps-section">
            <div className="section-header">
              <span className="section-icon warning-icon">{icon(<><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></>)}</span>
              <h2>Traps to Avoid</h2>
            </div>
            <div className="traps-list">
              {plan.traps.map((trap, idx) => (
                <div key={idx} className="trap-item">
                  <span className="trap-icon">!</span>
                  <span>{trap.trap}{trap.instead ? `. Instead: ${trap.instead}` : ''}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="plan-footer">
          <div className="footer-quote">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
            <p>Perfect practice makes perfect. 10 focused turns beat 100 mindless ones!</p>
          </div>
        </div>
      </div>
    )
  }

  // Clear all and start over
  const clearAll = () => {
    setSelectedFiles([])
    setAnalysis(null)
    setLoadedLogId(null)
    setEditingValues(false)
    setTrainingPlan(null)
    setError(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  // Get Ski:IQ display value
  const getSkiIQ = () => {
    if (!analysis?.session_overview?.ski_iq_range) return null
    const range = analysis.session_overview.ski_iq_range
    if (range.average) return range.average
    if (range.highest) return range.highest
    return null
  }

  const skiIQ = getSkiIQ()

  // Save current session to progress log (database primary, localStorage cache)
  const saveToProgressLog = async () => {
    if (!analysis || savingSession) return

    setSavingSession(true)
    try {
      // Use session_datetime from CARV screenshot as master timestamp (priority 1)
      // Fall back to sessionDateTime (from EXIF/filename) (priority 2)
      // Finally fall back to current time (priority 3)
      // All three are local wall-clock times with no timezone, so the database
      // stores the time shown on the CARV screen rather than a UTC-shifted one
      const masterDateTime = normaliseLocalDateTime(analysis?.session_overview?.session_datetime) ||
        normaliseLocalDateTime(sessionDateTime) ||
        toLocalInputValue()

      // Store the scoring version explicitly so later comparisons don't depend on a guess
      const analysisToSave = {
        ...analysis,
        session_overview: {
          ...(analysis.session_overview || {}),
          ski_iq_version: getSkiIQVersion(analysis, masterDateTime)
        }
      }

      // Same run saved before (same time and, when known, same location)? The same Carv
      // run can be screenshotted twice, e.g. before and after the Carv app 10 update.
      const location = analysis?.session_overview?.location || null
      // A session opened from history is updated in place, even if its date was corrected
      const openedLog = loadedLogId ? progressLogs.find(log => log.id === loadedLogId) : null
      const existingLog = openedLog || progressLogs.find(log =>
        normaliseLocalDateTime(log.datetime) === masterDateTime &&
        (!location || !log.analysis?.session_overview?.location || log.analysis.session_overview.location === location)
      )
      if (existingLog && !openedLog && !window.confirm(
        `A session at ${formatDateTime(existingLog.datetime, existingLog.datetimeDisplay)} is already saved. Replace it with this analysis?\n\nOK replaces it. Cancel keeps the existing one and does not save.`
      )) {
        setShowSaveDialog(false)
        return
      }

      const sessionPayload = {
        session_date: masterDateTime,
        location,
        metrics: analysisToSave,
        training_plan: serializePlan(trainingPlan),
        notes: sessionNotes
      }

      // Save to database first (source of truth): update the existing copy or create a new one
      let dbSession = null
      try {
        if (existingLog?.dbSessionId) {
          const response = await axios.put(`${API_BASE_URL}/sessions/${existingLog.dbSessionId}`, sessionPayload)
          dbSession = response.data
          setDbSessions(prev => prev.map(s => s.id === dbSession.id ? dbSession : s))
        } else {
          dbSession = await saveSessionToDb(sessionPayload)
        }
      } catch (err) {
        console.warn('Database save failed, saving to localStorage only:', err)
      }

      // The server's copy has the grouped fields and averages recomputed
      const savedAnalysis = dbSession?.metrics || analysisToSave
      const logEntry = {
        id: dbSession?.id || `log-${Date.now()}`,
        dbSessionId: dbSession?.id || null,
        datetime: masterDateTime,
        datetimeDisplay: analysis?.session_overview?.session_date_display || null,
        datetimeSource: analysis?.session_overview?.session_datetime ? 'carv_screenshot' :
          (sessionDateTime ? 'exif_or_filename' : 'current_time'),
        notes: sessionNotes,
        savedAt: new Date().toISOString(),
        analysis: savedAnalysis,
        trainingPlan: trainingPlan,
        metrics: {
          skiIQ: skiIQ,
          balance: savedAnalysis.overall_metrics?.balance,
          edging: savedAnalysis.overall_metrics?.edging,
          rotary: savedAnalysis.overall_metrics?.rotary,
          performance: savedAnalysis.overall_metrics?.performance
        }
      }

      // Save to localStorage as cache (no base64 previews); a replaced run swaps in place
      setProgressLogs(prev => existingLog
        ? prev.map(log => log.id === existingLog.id ? { ...logEntry, id: existingLog.id, dbSessionId: dbSession?.id || existingLog.dbSessionId } : log)
        : [...prev, logEntry])
      setAnalysis(savedAnalysis)
      setLoadedLogId(existingLog ? existingLog.id : logEntry.id)

      setShowSaveDialog(false)
      setSessionNotes('')
    } catch (err) {
      console.error('Error saving to progress log:', err)
      setError('Failed to save session. Please try again.')
      setShowSaveDialog(false)
    } finally {
      setSavingSession(false)
    }
  }

  // Delete a progress log entry (from both localStorage and database)
  const deleteProgressLog = async (logId) => {
    const logToDelete = progressLogs.find(log => log.id === logId)

    // Delete the database copy first; if that fails keep the local entry,
    // otherwise the session would silently come back on the next load
    const dbId = logToDelete?.dbSessionId
    if (dbId) {
      try {
        await deleteSessionFromDb(dbId)
      } catch (err) {
        setError('Could not delete this session from the cloud database, so it has been kept. Please try again.')
        setDeleteConfirmId(null)
        return
      }
    }

    // Remove from localStorage state
    setProgressLogs(prev => prev.filter(log => log.id !== logId))

    if (selectedLogForComparison?.id === logId) {
      setSelectedLogForComparison(null)
    }

    setDeleteConfirmId(null)
  }

  // Load a previous log for viewing
  const loadProgressLog = (log) => {
    // Warn if current analysis hasn't been saved
    if (analysis && !progressLogs.some(l => l.analysis === analysis)) {
      if (!window.confirm('You have an unsaved analysis. Loading a previous session will overwrite it. Continue?')) {
        return
      }
    }
    setAnalysis(log.analysis)
    setLoadedLogId(log.id)
    setSessionNotes(log.notes || '') // so saving again keeps the session's notes
    setEditingValues(false)
    setTrainingPlan(parseStoredPlan(log.trainingPlan))
    setSessionDateTime(normaliseLocalDateTime(log.datetime) || '')
    setShowProgressPanel(false)
  }

  // Get metric value from a log
  const getMetricFromLog = (log, category, metric) => {
    if (!log?.metrics?.[category]) return null
    if (metric === 'category_average') {
      return log.metrics[category]?.category_average
    }
    return log.metrics[category]?.[metric]
  }

  // Format datetime for display
  const formatDateTime = (isoString, displayString = null) => {
    // If we have a display string from CARV screenshot, use it
    if (displayString) return displayString

    if (!isoString) return 'Unknown date'
    const date = new Date(isoString)
    return date.toLocaleDateString('en-US', {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  // Calculate metric change between two logs
  const getMetricChange = (currentValue, previousValue) => {
    if (currentValue === null || previousValue === null) return null
    return currentValue - previousValue
  }

  // Open comparison modal with two logs
  const openComparison = (logA, logB) => {
    // Ensure logA is the older one (baseline), logB is newer
    const dateA = new Date(logA.datetime)
    const dateB = new Date(logB.datetime)
    if (dateA > dateB) {
      setComparisonLogA(logB)
      setComparisonLogB(logA)
    } else {
      setComparisonLogA(logA)
      setComparisonLogB(logB)
    }
    setShowComparisonModal(true)
  }

  // Close comparison modal
  const closeComparison = () => {
    setShowComparisonModal(false)
    setComparisonLogA(null)
    setComparisonLogB(null)
    setSelectedLogForComparison(null)
  }

  // Render comparison metric row
  const renderComparisonRow = (label, valueA, valueB, unit = '', neutral = false) => {
    const hasA = valueA !== null && valueA !== undefined
    const hasB = valueB !== null && valueB !== undefined
    const diff = hasA && hasB ? valueB - valueA : null
    const percentChange = hasA && valueA !== 0 && diff ? ((diff / valueA) * 100) : null
    const decimals = unit === 'g' ? 1 : 0
    const formatDiff = (d) => `${d > 0 ? '+' : ''}${d.toFixed(decimals)}`
    return (
      <div className="comparison-row">
        <span className="comparison-label">{label}</span>
        <span className="comparison-value old">{hasA ? formatMetricValue(valueA, unit) : '-'}</span>
        <span className={`comparison-diff ${neutral ? '' : diff > 0 ? 'positive' : diff < 0 ? 'negative' : ''}`}>
          {diff !== null ? (
            <>
              {formatDiff(diff)}
              {percentChange !== null && Math.abs(percentChange) >= 1 && (
                <small> ({percentChange > 0 ? '+' : ''}{percentChange.toFixed(1)}%)</small>
              )}
            </>
          ) : '-'}
        </span>
        <span className="comparison-value new">{hasB ? formatMetricValue(valueB, unit) : '-'}</span>
      </div>
    )
  }

  // Get sorted logs (newest first)
  const sortedLogs = [...progressLogs].sort((a, b) =>
    new Date(b.datetime) - new Date(a.datetime)
  )

  // Sessions saved only in this browser (not yet in the cloud database)
  const unsyncedLogCount = progressLogs.filter(log => !log.dbSessionId).length

  // Login screen
  if (!isAuthenticated) {
    return (
      <div className="login-screen">
        <div className="login-container">
          <div className="login-header">
            <h1>CARV Analyzer</h1>
            <p>Personal Ski Training Coach</p>
          </div>
          <form onSubmit={handleLogin} className="login-form">
            <div className="login-field">
              <label htmlFor="password">Enter Password</label>
              <input
                type="password"
                id="password"
                value={passwordInput}
                onChange={(e) => setPasswordInput(e.target.value)}
                placeholder="Password"
                autoFocus
              />
            </div>
            {authError && <div className="login-error">{authError}</div>}
            <button type="submit" className="login-btn" disabled={loggingIn}>
              {loggingIn ? 'Signing in...' : 'Enter'}
            </button>
          </form>
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-content">
          <h1>CARV Analyzer</h1>
          <p>Upload your CARV screenshots for a complete analysis of your skiing session</p>
        </div>
        <button
          className="progress-toggle-btn"
          onClick={() => {
            if (!showProgressPanel) {
              // Refresh DB sessions when opening panel
              axios.get(`${API_BASE_URL}/sessions`).then(res => setDbSessions(res.data)).catch(() => {})
            }
            setShowProgressPanel(!showProgressPanel)
          }}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 12V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h7"/>
            <path d="M16 2v4"/>
            <path d="M8 2v4"/>
            <path d="M3 10h18"/>
            <path d="M18 21l3-3-3-3"/>
            <path d="M21 18H15"/>
          </svg>
          Progress Log {progressLogs.length > 0 && <span className="log-count">{progressLogs.length}</span>}
        </button>
      </header>

      {/* Main Content */}
      <main className="main-content">
        {/* Upload Section */}
        <section className="upload-section">
          <div
            className="drop-zone"
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragEnter={handleDragEnter}
            onClick={() => fileInputRef.current?.click()}
          >
            <div className="drop-zone-content">
              <div className="drop-icon">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
              </div>
              <p className="drop-text">Drag & drop your CARV screenshots here</p>
              <p className="drop-subtext">Upload multiple screenshots for a complete session analysis</p>
              <p className="file-types">Supports: PNG, JPG, WEBP (max 5MB each)</p>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/jpg,image/webp"
              onChange={handleFileSelect}
              className="file-input"
              multiple
            />
          </div>

          {/* Selected Files Preview */}
          {selectedFiles.length > 0 && (
            <div className="selected-files">
              <div className="selected-files-header">
                <h3>{selectedFiles.length} screenshot{selectedFiles.length !== 1 ? 's' : ''} ready for analysis</h3>
                <button className="clear-all-btn" onClick={clearAll}>Clear All</button>
              </div>
              <div className="files-preview-grid">
                {selectedFiles.map((fileObj, index) => (
                  <div key={fileObj.id} className="file-preview-card">
                    <div className="file-number">{index + 1}</div>
                    <img src={fileObj.preview} alt={fileObj.name} />
                    <button
                      className="remove-file-btn"
                      onClick={(e) => { e.stopPropagation(); removeFile(fileObj.id); }}
                    >
                      ×
                    </button>
                    <span className="file-name">{fileObj.name}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Action Buttons */}
          <div className="action-buttons">
            <button
              className="btn btn-primary"
              onClick={analyzeAllScreenshots}
              disabled={selectedFiles.length === 0 || loading}
            >
              {loading ? (
                <>
                  <span className="spinner"></span>
                  Analyzing {selectedFiles.length} screenshot{selectedFiles.length !== 1 ? 's' : ''}...
                </>
              ) : (
                `Analyze ${selectedFiles.length || ''} Screenshot${selectedFiles.length !== 1 ? 's' : ''} Together`
              )}
            </button>
            {analysis && (
              <>
                <div className="plan-options">
                  <label>
                    Ski days
                    <input type="number" min="1" max="7" value={planDays}
                      onChange={(e) => setPlanDays(Math.min(7, Math.max(1, Number(e.target.value) || 1)))} />
                  </label>
                  <label>
                    Runs a day
                    <input type="number" min="4" max="20" value={planRuns}
                      onChange={(e) => setPlanRuns(Math.min(20, Math.max(4, Number(e.target.value) || 4)))} />
                  </label>
                </div>
                <button
                  className="btn btn-secondary"
                  onClick={generateTrainingPlan}
                  disabled={planLoading}
                >
                  {planLoading ? (
                    <>
                      <span className="spinner"></span>
                      Generating Plan...
                    </>
                  ) : (
                    'Generate Training Plan'
                  )}
                </button>
                <button
                  className="btn btn-save"
                  onClick={() => setShowSaveDialog(true)}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
                    <polyline points="17 21 17 13 7 13 7 21"/>
                    <polyline points="7 3 7 8 15 8"/>
                  </svg>
                  Save to Progress Log
                </button>
                <button
                  className="btn btn-export"
                  onClick={exportAnalysisToFile}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                  </svg>
                  Export to File
                </button>
              </>
            )}
          </div>

          {/* Error Message */}
          {error && (
            <div className="error-message">
              <span className="error-icon">!</span>
              {error}
            </div>
          )}
        </section>

        {/* Analysis Results */}
        {analysis && (
          <>
            {/* Session Overview Banner */}
            <section className="session-overview-section">
              <div className="session-banner">
                {skiIQ && (
                  <div className="ski-iq-display">
                    <div className="ski-iq-label">Ski:IQ</div>
                    <div className="ski-iq-score">{Math.round(skiIQ)}</div>
                    <div className="ski-iq-level">
                      {skiIQ >= 140 ? 'Expert' :
                       skiIQ >= 125 ? 'Advanced' :
                       skiIQ >= 100 ? 'Intermediate' : 'Beginner'}
                    </div>
                  </div>
                )}
                <div className="session-stats">
                  <div className="stat">
                    <button type="button" className="edit-values-btn" onClick={() => setEditingValues(v => !v)}>
                      {editingValues ? 'Done editing' : 'Edit values'}
                    </button>
                    {analysis.user_corrected?.length > 0 && <span className="stat-label">Corrected by you</span>}
                  </div>
                  <div className="stat">
                    <label className="version-select-label" htmlFor="ski-iq-version">Scoring</label>
                    <select
                      id="ski-iq-version"
                      className="version-select"
                      value={getSkiIQVersion(analysis, sessionDateTime) || ''}
                      onChange={(e) => setAnalysisSkiIQVersion(e.target.value)}
                      title="Ski:IQ 26 (Stelvio) rescored off-piste runs about 4.5 points higher. Change this if the guess is wrong."
                    >
                      <option value="stelvio">Ski:IQ 26 (Stelvio)</option>
                      <option value="wasatch">Pre-26 (Wasatch)</option>
                    </select>
                  </div>
                  <div className="stat">
                    <span className="stat-value">{analysis.num_screenshots}</span>
                    <span className="stat-label">Screenshots Analyzed</span>
                  </div>
                  {analysis.session_overview?.total_turns_analyzed && (
                    <div className="stat">
                      <span className="stat-value">{analysis.session_overview.total_turns_analyzed}</span>
                      <span className="stat-label">Total Turns</span>
                    </div>
                  )}
                  {analysis.session_overview?.terrain_types_seen?.length > 0 && (
                    <div className="stat">
                      <span className="stat-value">{analysis.session_overview.terrain_types_seen.join(', ')}</span>
                      <span className="stat-label">Terrain</span>
                    </div>
                  )}
                </div>
              </div>
            </section>

            {/* Holistic Analysis */}
            {analysis.holistic_analysis && (
              <section className="holistic-section">
                <h2>Your Skiing Profile</h2>
                <div className="holistic-grid">
                  <div className="holistic-card style-card">
                    <h3>Skiing Style</h3>
                    <p>{analysis.holistic_analysis.skiing_style}</p>
                  </div>
                  <div className="holistic-card signature-card">
                    <h3>Your Technique Signature</h3>
                    <p>{analysis.holistic_analysis.technique_signature}</p>
                  </div>
                  <div className="holistic-card consistency-card">
                    <h3>Consistency</h3>
                    <p>{analysis.holistic_analysis.consistency_assessment}</p>
                  </div>
                  <div className="holistic-card limiter-card">
                    <h3>Biggest Limiter</h3>
                    <p>{analysis.holistic_analysis.biggest_limiter}</p>
                  </div>
                  <div className="holistic-card strength-card">
                    <h3>Hidden Strength</h3>
                    <p>{analysis.holistic_analysis.hidden_strength}</p>
                  </div>
                </div>
              </section>
            )}

            {/* Detailed Observations */}
            {analysis.detailed_observations && (
              <section className="observations-section">
                <div className="observations-card">
                  <h3>Detailed Analysis</h3>
                  <p>{analysis.detailed_observations}</p>
                </div>
              </section>
            )}

            {editingValues && renderValueEditor()}

            {/* Carv metrics as Carv shows them (current analyses) */}
            {(analysis.carv_metrics || analysis.run_details) ? (
              <section className="metrics-section">
                <h2>Carv Metrics</h2>
                {renderRunDetails(analysis.run_details)}
                {renderCarvMetrics(analysis.carv_metrics)}
              </section>
            ) : analysis.overall_metrics && (
              <section className="metrics-section">
                <h2>Overall Metrics</h2>
                <div className="metrics-grid">
                  {renderMetricCard('Balance', analysis.overall_metrics.balance, '⚖️')}
                  {renderMetricCard('Edging', analysis.overall_metrics.edging, '🎿')}
                  {renderMetricCard('Rotary', analysis.overall_metrics.rotary, '🔄')}
                  {renderMetricCard('Performance', analysis.overall_metrics.performance, '⚡')}
                </div>
              </section>
            )}

            {/* Top 3 Strengths & Priorities */}
            <section className="priorities-section">
              <div className="priorities-grid">
                {/* Strengths */}
                {analysis.top_3_strengths && analysis.top_3_strengths.length > 0 && (
                  <div className="priorities-card strengths-priorities">
                    <h3>Top Strengths</h3>
                    <div className="priority-list">
                      {analysis.top_3_strengths.map((strength, idx) => (
                        <div key={idx} className="priority-item">
                          <div className="priority-header">
                            <span className="priority-rank">{idx + 1}</span>
                            <span className="priority-area">{strength.area}</span>
                            {strength.score && (
                              <span className={`priority-score ${getScoreColor(strength.score)}`}>
                                {Math.round(strength.score)}
                              </span>
                            )}
                          </div>
                          <p className="priority-detail">{strength.why_it_matters}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Priorities */}
                {analysis.top_3_priorities && analysis.top_3_priorities.length > 0 && (
                  <div className="priorities-card focus-priorities">
                    <h3>Top Priorities to Work On</h3>
                    <div className="priority-list">
                      {analysis.top_3_priorities.map((priority, idx) => (
                        <div key={idx} className="priority-item">
                          <div className="priority-header">
                            <span className="priority-rank">{idx + 1}</span>
                            <span className="priority-area">{priority.area}</span>
                            {priority.current_score && (
                              <span className="priority-scores">
                                <span className={getScoreColor(priority.current_score)}>
                                  {Math.round(priority.current_score)}
                                </span>
                                <span className="arrow">→</span>
                                <span className="target-score">{Math.round(priority.target_score)}</span>
                              </span>
                            )}
                          </div>
                          <p className="priority-why">{priority.why_priority}</p>
                          <p className="priority-quick-win">
                            <strong>Quick win:</strong> {priority.quick_win}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </section>

            {/* Run by Run Notes */}
            {analysis.run_by_run_notes && analysis.run_by_run_notes.length > 0 && (
              <section className="run-notes-section">
                <h2>Screenshot-by-Screenshot Notes</h2>
                <div className="run-notes-grid">
                  {analysis.run_by_run_notes.map((note, idx) => (
                    <div key={idx} className="run-note-card">
                      <div className="run-note-number">Screenshot {note.screenshot}</div>
                      <p>{note.key_observation}</p>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        )}

        {/* Training Plan Section */}
        {trainingPlan && (
          <section className="training-plan-section">
            <div className="plan-section-header">
              <div className="plan-title-badge">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
                  <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
                </svg>
              </div>
              <h2>Your Personalized Training Plan</h2>
            </div>
            {renderTrainingPlan(trainingPlan)}
          </section>
        )}
      </main>

      {/* Save Dialog Modal */}
      {showSaveDialog && (
        <div className="modal-overlay" onClick={() => setShowSaveDialog(false)}>
          <div className="save-dialog" onClick={e => e.stopPropagation()}>
            <div className="save-dialog-header">
              <h3>Save to Progress Log</h3>
              <button className="close-btn" onClick={() => setShowSaveDialog(false)}>×</button>
            </div>
            <div className="save-dialog-body">
              <div className="form-group">
                <label>Session Date & Time</label>
                <input
                  type="datetime-local"
                  value={sessionDateTime}
                  onChange={(e) => setSessionDateTime(e.target.value)}
                  className="datetime-input"
                />
                <span className="helper-text">
                  {analysis?.session_overview?.session_datetime
                    ? `Extracted from CARV screenshot${analysis?.session_overview?.session_date_display ? `: ${analysis.session_overview.session_date_display}` : ''}`
                    : 'Editable - set from photo metadata or enter manually'}
                </span>
              </div>
              <div className="form-group">
                <label>Session Notes (optional)</label>
                <textarea
                  value={sessionNotes}
                  onChange={(e) => setSessionNotes(e.target.value)}
                  placeholder="e.g., Fresh powder, worked on edge angles..."
                  className="notes-input"
                  rows={3}
                />
              </div>
              <div className="save-preview">
                <div className="preview-metric">
                  <span className="preview-label">Ski:IQ</span>
                  <span className="preview-value">{skiIQ ? Math.round(skiIQ) : 'N/A'}</span>
                </div>
                <div className="preview-metric">
                  <span className="preview-label">Screenshots</span>
                  <span className="preview-value">{analysis?.num_screenshots || 0}</span>
                </div>
              </div>
            </div>
            <div className="save-dialog-footer">
              <button className="btn btn-cancel" onClick={() => setShowSaveDialog(false)} disabled={savingSession}>Cancel</button>
              <button className="btn btn-save-confirm" onClick={saveToProgressLog} disabled={savingSession}>
                {savingSession ? (
                  <>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="spinning">
                      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
                    </svg>
                    Saving...
                  </>
                ) : (
                  <>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    Save Session
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Progress Panel Sidebar */}
      {showProgressPanel && (
        <div className="progress-panel-overlay" onClick={() => setShowProgressPanel(false)}>
          <div className="progress-panel" onClick={e => e.stopPropagation()}>
            <div className="progress-panel-header">
              <h2>Progress History</h2>
              <div className="panel-header-actions">
                <span className="session-badge">{sortedLogs.length} session{sortedLogs.length !== 1 ? 's' : ''}</span>
                <button className="close-btn" onClick={() => setShowProgressPanel(false)}>×</button>
              </div>
            </div>

            {sortedLogs.length === 0 ? (
              <div className="empty-logs">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
                  <line x1="16" y1="2" x2="16" y2="6"/>
                  <line x1="8" y1="2" x2="8" y2="6"/>
                  <line x1="3" y1="10" x2="21" y2="10"/>
                </svg>
                <p>No sessions logged yet</p>
                <span>Analyze your CARV screenshots and save them to track progress over time</span>
              </div>
            ) : (
              <div className="progress-panel-content">
                {/* Migration Banner - Show if local logs exist but not all are in DB */}
                {unsyncedLogCount > 0 && (
                  <div className="migration-banner">
                    <div className="migration-info">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="17 8 12 3 7 8"/>
                        <line x1="12" y1="3" x2="12" y2="15"/>
                      </svg>
                      <span>
                        {unsyncedLogCount} session{unsyncedLogCount !== 1 ? 's' : ''} only on this device
                        {dbLoadFailed && ' (cloud database unreachable)'}
                      </span>
                    </div>
                    <button
                      className="migrate-btn"
                      onClick={migrateLogsToDatabase}
                      disabled={migrating}
                    >
                      {migrating ? 'Uploading...' : 'Sync to Cloud'}
                    </button>
                  </div>
                )}

                {/* Migration Result */}
                {migrationResult && (
                  <div className={`migration-result ${migrationResult.success ? 'success' : 'error'}`}>
                    {migrationResult.message}
                    <button onClick={() => setMigrationResult(null)}>×</button>
                  </div>
                )}

                {/* AI Progression Analysis Button */}
                {dbSessions.length >= 2 && (
                  <div className="progression-analysis-section">
                    <button
                      className="progression-btn"
                      onClick={() => analyzeProgression()}
                      disabled={loadingProgression}
                    >
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M12 20V10"/>
                        <path d="M18 20V4"/>
                        <path d="M6 20v-4"/>
                      </svg>
                      {loadingProgression ? 'Analyzing...' : 'AI Progression Analysis'}
                    </button>
                  </div>
                )}

                {/* Comparison Selection */}
                {sortedLogs.length > 1 && selectedLogForComparison && (
                  <div className="comparison-banner">
                    <span>Comparing with: {formatDateTime(selectedLogForComparison.datetime, selectedLogForComparison.datetimeDisplay)}</span>
                    <button onClick={() => setSelectedLogForComparison(null)}>Clear</button>
                  </div>
                )}

                {/* Progression Line Chart */}
                {sortedLogs.length > 1 && (
                  <div className="progress-chart-container">
                    <div className="chart-header">
                      <h3>Progression Over Time</h3>
                      <div className="metric-selector">
                        <select
                          value={selectedMetric}
                          onChange={(e) => setSelectedMetric(e.target.value)}
                          className="metric-select"
                        >
                          <option value="skiIQ">Ski:IQ</option>
                          <option value="balance">Balance</option>
                          <option value="edging">Edging</option>
                          <option value="rotary">Rotary</option>
                          <option value="gforce">G-Force</option>
                        </select>
                      </div>
                    </div>
                    <div className="line-chart-container">
                      {(() => {
                        const chartLogs = [...sortedLogs].reverse()
                        const getMetricValue = (log) => {
                          switch(selectedMetric) {
                            case 'skiIQ': return log.metrics.skiIQ
                            case 'balance': return log.metrics.balance?.category_average
                            case 'edging': return log.metrics.edging?.category_average
                            case 'rotary': return log.metrics.rotary?.category_average
                            case 'gforce': return log.metrics.performance?.turn_g_force
                            default: return log.metrics.skiIQ
                          }
                        }
                        const values = chartLogs.map(l => getMetricValue(l)).filter(v => v != null)
                        if (values.length < 2) return <div className="no-chart-data">Not enough data for chart</div>

                        const minVal = Math.min(...values) - 5
                        const maxVal = Math.max(...values) + 5
                        const range = maxVal - minVal || 20
                        const width = 340
                        const height = 120
                        const padding = { top: 10, right: 10, bottom: 25, left: 35 }
                        const chartWidth = width - padding.left - padding.right
                        const chartHeight = height - padding.top - padding.bottom

                        const points = chartLogs.map((log, idx) => {
                          const val = getMetricValue(log)
                          if (val == null) return null
                          const x = padding.left + (idx / (chartLogs.length - 1)) * chartWidth
                          const y = padding.top + chartHeight - ((val - minVal) / range) * chartHeight
                          return { x, y, val, date: log.datetime, log }
                        }).filter(Boolean)

                        const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')
                        const areaD = `${pathD} L ${points[points.length-1].x} ${height - padding.bottom} L ${points[0].x} ${height - padding.bottom} Z`

                        // Calculate trend
                        const firstVal = values[0]
                        const lastVal = values[values.length - 1]
                        const trend = lastVal - firstVal

                        // Where Ski:IQ scoring switched to Ski:IQ 26 (Stelvio): scores either side aren't directly comparable
                        const versions = chartLogs.map(l => getSkiIQVersion(l.analysis, l.datetime))
                        const boundaryIdx = versions.findIndex((v, i) => i > 0 && v === 'stelvio' && versions[i - 1] === 'wasatch')
                        const boundaryX = selectedMetric === 'skiIQ' && boundaryIdx > 0
                          ? padding.left + ((boundaryIdx - 0.5) / (chartLogs.length - 1)) * chartWidth
                          : null

                        return (
                          <svg viewBox={`0 0 ${width} ${height}`} className="line-chart-svg">
                            {boundaryX !== null && (
                              <g>
                                <line x1={boundaryX} y1={padding.top} x2={boundaryX} y2={height - padding.bottom}
                                  stroke="#667eea" strokeWidth="1" strokeDasharray="3 3" />
                                <text x={boundaryX + 3} y={padding.top + 8} fontSize="8" fill="#667eea">Ski:IQ 26</text>
                                <title>Carv changed Ski:IQ scoring here (Ski:IQ 26 Stelvio). Off-piste scores rose about 4.5 points for the same skiing.</title>
                              </g>
                            )}
                            {/* Grid lines */}
                            {[0, 0.25, 0.5, 0.75, 1].map((pct, i) => (
                              <g key={i}>
                                <line
                                  x1={padding.left}
                                  y1={padding.top + chartHeight * pct}
                                  x2={width - padding.right}
                                  y2={padding.top + chartHeight * pct}
                                  stroke="#e0e0e0"
                                  strokeWidth="1"
                                />
                                <text
                                  x={padding.left - 5}
                                  y={padding.top + chartHeight * pct + 4}
                                  textAnchor="end"
                                  fontSize="9"
                                  fill="#999"
                                >
                                  {Math.round(maxVal - (range * pct))}
                                </text>
                              </g>
                            ))}

                            {/* Gradient fill under line */}
                            <defs>
                              <linearGradient id="chartGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                                <stop offset="0%" stopColor={trend >= 0 ? "#28a745" : "#dc3545"} stopOpacity="0.3"/>
                                <stop offset="100%" stopColor={trend >= 0 ? "#28a745" : "#dc3545"} stopOpacity="0.05"/>
                              </linearGradient>
                            </defs>
                            <path d={areaD} fill="url(#chartGradient)" />

                            {/* Line */}
                            <path
                              d={pathD}
                              fill="none"
                              stroke={trend >= 0 ? "#28a745" : "#dc3545"}
                              strokeWidth="2.5"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />

                            {/* Data points */}
                            {points.map((p, i) => (
                              <g key={i}>
                                <circle
                                  cx={p.x}
                                  cy={p.y}
                                  r="5"
                                  fill="white"
                                  stroke={trend >= 0 ? "#28a745" : "#dc3545"}
                                  strokeWidth="2"
                                  className="chart-point"
                                />
                                <title>{`${new Date(p.date).toLocaleDateString()}: ${Math.round(p.val)}`}</title>
                                {/* Date labels for first, middle, last */}
                                {(i === 0 || i === points.length - 1 || (points.length > 4 && i === Math.floor(points.length / 2))) && (
                                  <text
                                    x={p.x}
                                    y={height - 5}
                                    textAnchor="middle"
                                    fontSize="8"
                                    fill="#666"
                                  >
                                    {new Date(p.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                                  </text>
                                )}
                              </g>
                            ))}
                          </svg>
                        )
                      })()}
                    </div>
                    <div className="chart-summary">
                      {(() => {
                        const getMetricValue = (log) => {
                          switch(selectedMetric) {
                            case 'skiIQ': return log.metrics.skiIQ
                            case 'balance': return log.metrics.balance?.category_average
                            case 'edging': return log.metrics.edging?.category_average
                            case 'rotary': return log.metrics.rotary?.category_average
                            case 'gforce': return log.metrics.performance?.turn_g_force
                            default: return log.metrics.skiIQ
                          }
                        }
                        const chartLogs = [...sortedLogs].reverse()
                        const values = chartLogs.map(l => getMetricValue(l)).filter(v => v != null)
                        if (values.length < 2) return null
                        const firstVal = values[0]
                        const lastVal = values[values.length - 1]
                        const trend = lastVal - firstVal
                        const metricLabels = { skiIQ: 'Ski:IQ', balance: 'Balance', edging: 'Edging', rotary: 'Rotary', gforce: 'G-Force' }
                        return (
                          <>
                            <span className="summary-label">{metricLabels[selectedMetric]} Trend:</span>
                            <span className={`summary-value ${trend >= 0 ? 'positive' : 'negative'}`}>
                              {trend >= 0 ? '+' : ''}{trend.toFixed(1)} pts
                            </span>
                            <span className="summary-range">({Math.round(firstVal)} → {Math.round(lastVal)})</span>
                            {selectedMetric === 'skiIQ' && new Set(chartLogs.map(l => getSkiIQVersion(l.analysis, l.datetime)).filter(Boolean)).size > 1 && (
                              <span className="summary-note">Includes Ski:IQ 26 rescoring: off-piste scores rose about 4.5 points for the same skiing.</span>
                            )}
                          </>
                        )
                      })()}
                    </div>
                  </div>
                )}

                {/* Logs List - All Sessions */}
                <div className="logs-list">
                  <div className="logs-list-header">
                    <span>All Sessions ({sortedLogs.length})</span>
                  </div>
                  {sortedLogs.map((log, idx) => {
                    const prevLog = sortedLogs[idx + 1] // Previous chronological log
                    const skiIQChange = prevLog ? getMetricChange(log.metrics.skiIQ, prevLog.metrics.skiIQ) : null
                    const isExpanded = expandedLogId === log.id

                    return (
                      <div key={log.id} className={`log-card ${isExpanded ? 'expanded' : ''}`}>
                        <div
                          className="log-card-header clickable"
                          onClick={() => setExpandedLogId(isExpanded ? null : log.id)}
                        >
                          <div className="log-header-left">
                            <svg
                              width="12"
                              height="12"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              className={`expand-icon ${isExpanded ? 'rotated' : ''}`}
                            >
                              <polyline points="9 18 15 12 9 6"/>
                            </svg>
                            <div className="log-date">
                              {formatDateTime(log.datetime, log.datetimeDisplay)}
                            </div>
                          </div>
                          <div className="log-header-right">
                            {(() => {
                              const version = getSkiIQVersion(log.analysis, log.datetime)
                              const technique = log.analysis?.run_details?.technique
                              return (
                                <>
                                  {technique && <span className="log-tag">{technique}</span>}
                                  {version && <span className={`log-tag version-${version}`}>{SKI_IQ_VERSION_LABELS[version]}</span>}
                                </>
                              )
                            })()}
                            <div className="log-skiiq-badge">
                              <span className="skiiq-value">{log.metrics.skiIQ ? Math.round(log.metrics.skiIQ) : '?'}</span>
                              {skiIQChange !== null && (
                                <span className={`skiiq-change ${skiIQChange >= 0 ? 'positive' : 'negative'}`}>
                                  {skiIQChange >= 0 ? '+' : ''}{Math.round(skiIQChange)}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>

                        {isExpanded && (
                          <div className="log-card-body">
                            {/* Carv metrics with units for current analyses; grouped bars for older ones */}
                            {log.analysis?.carv_metrics ? renderCarvMetrics(log.analysis.carv_metrics) : (
                            <div className="log-metrics-expanded">
                              <div className="metric-row">
                                <span className="metric-name">Balance</span>
                                <div className="metric-bar-container">
                                  <div
                                    className="metric-bar"
                                    style={{ width: `${log.metrics.balance?.category_average || 0}%` }}
                                  />
                                </div>
                                <span className="metric-val">{log.metrics.balance?.category_average ? Math.round(log.metrics.balance.category_average) : '-'}</span>
                              </div>
                              <div className="metric-row">
                                <span className="metric-name">Edging</span>
                                <div className="metric-bar-container">
                                  <div
                                    className="metric-bar edging"
                                    style={{ width: `${log.metrics.edging?.category_average || 0}%` }}
                                  />
                                </div>
                                <span className="metric-val">{log.metrics.edging?.category_average ? Math.round(log.metrics.edging.category_average) : '-'}</span>
                              </div>
                              <div className="metric-row">
                                <span className="metric-name">Rotary</span>
                                <div className="metric-bar-container">
                                  <div
                                    className="metric-bar rotary"
                                    style={{ width: `${log.metrics.rotary?.category_average || 0}%` }}
                                  />
                                </div>
                                <span className="metric-val">{log.metrics.rotary?.category_average ? Math.round(log.metrics.rotary.category_average) : '-'}</span>
                              </div>
                              <div className="metric-row">
                                <span className="metric-name">G-Force</span>
                                <div className="metric-bar-container">
                                  <div
                                    className="metric-bar gforce"
                                    style={{ width: `${Math.min(100, ((log.metrics.performance?.turn_g_force || 0) / 3) * 100)}%` }}
                                  />
                                </div>
                                <span className="metric-val">{log.metrics.performance?.turn_g_force ? `${Number(log.metrics.performance.turn_g_force).toFixed(1)} g` : '-'}</span>
                              </div>
                            </div>
                            )}

                            {/* Notes */}
                            {log.notes && (
                              <div className="log-notes">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                  <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                </svg>
                                <span>{log.notes}</span>
                              </div>
                            )}

                            {/* Action Buttons */}
                            <div className="log-actions-expanded">
                              <button className="load-log-btn" onClick={() => loadProgressLog(log)}>
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                                  <circle cx="12" cy="12" r="3"/>
                                </svg>
                                View Analysis
                              </button>
                              {sortedLogs.length > 1 && (
                                <button
                                  className={`compare-log-btn ${selectedLogForComparison?.id === log.id ? 'active' : ''}`}
                                  onClick={() => {
                                    if (selectedLogForComparison) {
                                      if (selectedLogForComparison.id !== log.id) {
                                        openComparison(selectedLogForComparison, log)
                                      }
                                      setSelectedLogForComparison(null)
                                    } else {
                                      setSelectedLogForComparison(log)
                                    }
                                  }}
                                >
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <line x1="18" y1="20" x2="18" y2="10"/>
                                    <line x1="12" y1="20" x2="12" y2="4"/>
                                    <line x1="6" y1="20" x2="6" y2="14"/>
                                  </svg>
                                  {selectedLogForComparison?.id === log.id ? 'Cancel' : 'Compare'}
                                </button>
                              )}
                              <button
                                className="delete-log-btn"
                                onClick={() => {
                                  if (deleteConfirmId === log.id) {
                                    deleteProgressLog(log.id)
                                  } else {
                                    setDeleteConfirmId(log.id)
                                  }
                                }}
                              >
                                {deleteConfirmId === log.id ? (
                                  <>
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                      <polyline points="20 6 9 17 4 12"/>
                                    </svg>
                                    Confirm
                                  </>
                                ) : (
                                  <>
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                      <polyline points="3 6 5 6 21 6"/>
                                      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                                    </svg>
                                    Delete
                                  </>
                                )}
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Chat Button */}
      <button
        className="chat-toggle-btn"
        onClick={() => setShowChat(!showChat)}
        title="Ask Coach Carv"
      >
        {showChat ? (
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        ) : (
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        )}
      </button>

      {/* Chat Panel */}
      {showChat && (
        <div className="chat-panel">
          <div className="chat-header">
            <div className="chat-title">
              <span className="coach-icon">⛷️</span>
              <span>Coach Carv</span>
            </div>
            <button className="chat-close" onClick={() => setShowChat(false)}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18"/>
                <line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>

          <div className="chat-messages">
            {chatMessages.length === 0 && (
              <div className="chat-welcome">
                <p className="welcome-title">Hey Patrick! I'm Coach Carv</p>
                <p>Your personal AI skiing coach. {analysis ? "I can see your session data - ask me about your metrics!" : "Upload some CARV screenshots and I can analyze them with you."}</p>
                <ul>
                  <li>Your technique & personalized drills</li>
                  <li>CARV metrics interpretation</li>
                  <li>{analysis ? "Questions about your session" : "Carving biomechanics"}</li>
                  <li>Equipment tips for 173cm/71kg</li>
                </ul>
              </div>
            )}

            {chatMessages.map((msg, index) => (
              <div key={index} className={`chat-message ${msg.role}`}>
                {msg.role === 'assistant' && <span className="message-avatar">⛷️</span>}
                <div className="message-content">
                  {msg.role === 'assistant' ? renderContentWithVideos(msg.content) : msg.content}
                </div>
              </div>
            ))}

            {chatLoading && (
              <div className="chat-message assistant">
                <span className="message-avatar">⛷️</span>
                <div className="message-content typing">
                  <span></span><span></span><span></span>
                </div>
              </div>
            )}

            <div ref={chatMessagesEndRef} />
          </div>

          <div className="chat-input-container">
            <input
              type="text"
              className="chat-input"
              placeholder="Ask about technique, drills, metrics..."
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && sendChatMessage()}
              disabled={chatLoading}
            />
            <button
              className="chat-send"
              onClick={sendChatMessage}
              disabled={chatLoading || !chatInput.trim()}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
        </div>
      )}

      {/* Comparison Modal */}
      {showComparisonModal && comparisonLogA && comparisonLogB && (
        <div className="comparison-modal-overlay" onClick={closeComparison}>
          <div className="comparison-modal" onClick={(e) => e.stopPropagation()}>
            <div className="comparison-modal-header">
              <h2>Session Comparison</h2>
              <button className="modal-close" onClick={closeComparison}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18"/>
                  <line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            </div>

            <div className="comparison-dates">
              <div className="comparison-date baseline">
                <span className="date-label">Baseline</span>
                <span className="date-value">{formatDateTime(comparisonLogA.datetime, comparisonLogA.datetimeDisplay)}</span>
              </div>
              <div className="comparison-arrow">→</div>
              <div className="comparison-date current">
                <span className="date-label">Current</span>
                <span className="date-value">{formatDateTime(comparisonLogB.datetime, comparisonLogB.datetimeDisplay)}</span>
              </div>
            </div>

            <div className="comparison-content">
              {/* Overall Score */}
              <div className="comparison-section">
                <h3>Overall Performance</h3>
                <div className="comparison-header-row">
                  <span></span>
                  <span>Baseline</span>
                  <span>Change</span>
                  <span>Current</span>
                </div>
                {renderComparisonRow('Ski:IQ', comparisonLogA.metrics?.skiIQ, comparisonLogB.metrics?.skiIQ)}
                {getSkiIQVersion(comparisonLogA.analysis, comparisonLogA.datetime) !== getSkiIQVersion(comparisonLogB.analysis, comparisonLogB.datetime) && (
                  <p className="comparison-note">These sessions were scored with different Ski:IQ versions. Ski:IQ 26 raised off-piste scores by about 4.5 points for the same skiing, so part of any change may be the rescoring.</p>
                )}
              </div>

              {/* Carv metrics (Carv's list and units; older sessions use their saved fields) */}
              <div className="comparison-section">
                <h3>Carv Metrics</h3>
                <div className="comparison-header-row">
                  <span></span>
                  <span>Baseline</span>
                  <span>Change</span>
                  <span>Current</span>
                </div>
                {CARV_METRIC_DEFS.map(def => {
                  const valueA = getLogMetricValue(comparisonLogA, def)
                  const valueB = getLogMetricValue(comparisonLogB, def)
                  if (valueA === null && valueB === null) return null
                  // Turn Closure is judged by its zone and pitch is terrain, so neither change is "better" or "worse"
                  return <div key={def.key}>{renderComparisonRow(def.label, valueA, valueB, def.unit, def.zoneOnly || def.key === 'slope_pitch')}</div>
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* AI Progression Analysis Modal */}
      {showProgressionModal && (
        <div className="progression-modal-overlay" onClick={() => setShowProgressionModal(false)}>
          <div className="progression-modal" onClick={(e) => e.stopPropagation()}>
            <div className="progression-modal-header">
              <h2>AI Progression Analysis</h2>
              <button className="close-btn" onClick={() => setShowProgressionModal(false)}>×</button>
            </div>

            {loadingProgression ? (
              <div className="progression-loading">
                <div className="spinner"></div>
                <p>Analyzing your progression across {dbSessions.length} sessions...</p>
              </div>
            ) : progressionAnalysis ? (
              <div className="progression-content">
                {/* Session Selection */}
                {progressionAnalysis.available_sessions?.length > 0 && (
                  <div className="session-selection-section">
                    <h3>Sessions Analyzed</h3>
                    <p className="selection-hint">Select which sessions to include in the analysis:</p>
                    <div className="session-checkboxes">
                      {progressionAnalysis.available_sessions.map(session => (
                        <label key={session.id} className={`session-checkbox ${selectedSessionIds.includes(session.id) ? 'selected' : ''}`}>
                          <input
                            type="checkbox"
                            checked={selectedSessionIds.includes(session.id)}
                            onChange={() => toggleSessionSelection(session.id)}
                          />
                          <span className="session-info">
                            <span className="session-date">{session.date}</span>
                            {session.ski_iq && <span className="session-iq">IQ: {Math.round(session.ski_iq)}</span>}
                            {session.location && <span className="session-location">{session.location}</span>}
                          </span>
                        </label>
                      ))}
                    </div>
                    <button
                      className="rerun-btn"
                      onClick={rerunProgressionAnalysis}
                      disabled={selectedSessionIds.length < 2 || loadingProgression}
                    >
                      {loadingProgression ? 'Analyzing...' : `Re-analyze ${selectedSessionIds.length} sessions`}
                    </button>
                  </div>
                )}

                {/* Summary */}
                {progressionAnalysis.summary && (
                  <div className="progression-summary">
                    <h3>Summary</h3>
                    <p>{progressionAnalysis.summary}</p>
                  </div>
                )}

                {/* Key Insights - Most Important */}
                {progressionAnalysis.key_insights?.length > 0 && (
                  <div className="progression-section key-insights">
                    <h3>Key Insights</h3>
                    <ul className="insights-list">
                      {progressionAnalysis.key_insights.map((insight, i) => (
                        <li key={i}>{insight}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Metric Correlations - THE MAIN FEATURE */}
                {progressionAnalysis.metric_correlations?.length > 0 && (
                  <div className="progression-section correlations-section">
                    <h3>Metric Correlations</h3>
                    <p className="section-desc">When one metric changes, these other metrics tend to change too:</p>
                    <div className="correlations-list">
                      {progressionAnalysis.metric_correlations.map((corr, i) => (
                        <div key={i} className={`correlation-card ${corr.correlation}`}>
                          <div className="correlation-header">
                            <span className="metric-pair">
                              {corr.metric_a}
                              <span className={`correlation-arrow ${corr.correlation}`}>
                                {corr.correlation === 'positive' ? '↔' : corr.correlation === 'negative' ? '↕' : '·'}
                              </span>
                              {corr.metric_b}
                            </span>
                            <span className={`correlation-type ${corr.correlation}`}>
                              {corr.correlation === 'positive' ? 'Move Together' :
                               corr.correlation === 'negative' ? 'Inverse' : 'Independent'}
                            </span>
                            {corr.confidence && (
                              <span className={`confidence-badge ${corr.confidence}`}>{corr.confidence}</span>
                            )}
                          </div>
                          <p className="correlation-observation">{corr.observation}</p>
                          {corr.biomechanical_explanation && (
                            <p className="correlation-explanation">
                              <strong>Why:</strong> {corr.biomechanical_explanation}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Causality Hypotheses */}
                {progressionAnalysis.causality_hypotheses?.length > 0 && (
                  <div className="progression-section causality-section">
                    <h3>Cause & Effect Analysis</h3>
                    <p className="section-desc">What might be driving your technique changes:</p>
                    <div className="causality-list">
                      {progressionAnalysis.causality_hypotheses.map((hyp, i) => (
                        <div key={i} className="causality-card">
                          <div className="causality-flow">
                            <span className="cause">{hyp.cause}</span>
                            <span className="arrow">→</span>
                            <span className="effect">{hyp.effect}</span>
                          </div>
                          <p className="causality-explanation">{hyp.explanation}</p>
                          {hyp.evidence && (
                            <p className="causality-evidence"><em>Evidence: {hyp.evidence}</em></p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Sessions Breakdown - Raw Data */}
                {progressionAnalysis.sessions_breakdown?.length > 0 && (
                  <div className="progression-section breakdown-section">
                    <h3>Session-by-Session Metrics</h3>
                    <div className="breakdown-table-wrapper">
                      <table className="breakdown-table">
                        <thead>
                          <tr>
                            <th>Date</th>
                            <th>Ski:IQ</th>
                            {progressionAnalysis.sessions_breakdown[0]?.key_metrics &&
                              Object.keys(progressionAnalysis.sessions_breakdown[0].key_metrics).map(key => (
                                <th key={key}>{key.replace(/_/g, ' ')}</th>
                              ))
                            }
                          </tr>
                        </thead>
                        <tbody>
                          {progressionAnalysis.sessions_breakdown.map((session, i) => (
                            <tr key={i}>
                              <td>{session.date}</td>
                              <td>{session.ski_iq ? Math.round(session.ski_iq) : '-'}</td>
                              {session.key_metrics && Object.values(session.key_metrics).map((val, j) => (
                                <td key={j}>{val !== null && val !== undefined ? (typeof val === 'number' ? Math.round(val) : val) : '-'}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Metric Trends */}
                {progressionAnalysis.metric_trends && (
                  <div className="progression-section">
                    <h3>Metric Trends</h3>
                    <div className="trend-grid">
                      {progressionAnalysis.metric_trends.ski_iq && (
                        <div className={`trend-card ${progressionAnalysis.metric_trends.ski_iq.direction}`}>
                          <span className="trend-label">Ski:IQ</span>
                          <span className={`trend-direction ${progressionAnalysis.metric_trends.ski_iq.direction}`}>
                            {progressionAnalysis.metric_trends.ski_iq.direction === 'improving' ? '↑' :
                             progressionAnalysis.metric_trends.ski_iq.direction === 'declining' ? '↓' : '→'}
                            {' '}{progressionAnalysis.metric_trends.ski_iq.change || progressionAnalysis.metric_trends.ski_iq.direction}
                          </span>
                          {progressionAnalysis.metric_trends.ski_iq.values && (
                            <span className="trend-values">{progressionAnalysis.metric_trends.ski_iq.values.map(v => Math.round(v)).join(' → ')}</span>
                          )}
                        </div>
                      )}
                      {progressionAnalysis.metric_trends.edge_angle && (
                        <div className={`trend-card ${progressionAnalysis.metric_trends.edge_angle.direction}`}>
                          <span className="trend-label">Edge Angle</span>
                          <span className={`trend-direction ${progressionAnalysis.metric_trends.edge_angle.direction}`}>
                            {progressionAnalysis.metric_trends.edge_angle.direction === 'improving' ? '↑' :
                             progressionAnalysis.metric_trends.edge_angle.direction === 'declining' ? '↓' : '→'}
                            {' '}{progressionAnalysis.metric_trends.edge_angle.change || progressionAnalysis.metric_trends.edge_angle.direction}
                          </span>
                        </div>
                      )}
                      {progressionAnalysis.metric_trends.balance && (
                        <div className={`trend-card ${progressionAnalysis.metric_trends.balance.direction}`}>
                          <span className="trend-label">Balance</span>
                          <span className={`trend-direction ${progressionAnalysis.metric_trends.balance.direction}`}>
                            {progressionAnalysis.metric_trends.balance.direction === 'improving' ? '↑' :
                             progressionAnalysis.metric_trends.balance.direction === 'declining' ? '↓' : '→'}
                            {' '}{progressionAnalysis.metric_trends.balance.change || progressionAnalysis.metric_trends.balance.direction}
                          </span>
                        </div>
                      )}
                      {progressionAnalysis.metric_trends.other_metrics?.map((metric, i) => (
                        <div key={i} className={`trend-card ${metric.direction}`}>
                          <span className="trend-label">{metric.name}</span>
                          <span className={`trend-direction ${metric.direction}`}>
                            {metric.direction === 'improving' ? '↑' :
                             metric.direction === 'declining' ? '↓' : '→'}
                            {' '}{metric.change || metric.direction}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Technique Narrative */}
                {progressionAnalysis.technique_narrative && (
                  <div className="progression-section">
                    <h3>How Your Technique Is Evolving</h3>
                    <p className="narrative">{progressionAnalysis.technique_narrative}</p>
                  </div>
                )}

                {/* Recommendations */}
                {progressionAnalysis.recommendations && (
                  <div className="progression-section recommendations">
                    <h3>What To Focus On Next</h3>
                    {progressionAnalysis.recommendations.primary_focus && (
                      <div className="recommendation-item primary">
                        <span className="rec-label">Primary Focus</span>
                        <p>{progressionAnalysis.recommendations.primary_focus}</p>
                      </div>
                    )}
                    {progressionAnalysis.recommendations.secondary_focus && (
                      <div className="recommendation-item secondary">
                        <span className="rec-label">Secondary Focus</span>
                        <p>{progressionAnalysis.recommendations.secondary_focus}</p>
                      </div>
                    )}
                    {progressionAnalysis.recommendations.what_to_watch && (
                      <div className="recommendation-item watch">
                        <span className="rec-label">What To Watch</span>
                        <p>{progressionAnalysis.recommendations.what_to_watch}</p>
                      </div>
                    )}
                    {progressionAnalysis.recommendations.suggested_drills?.length > 0 && (
                      <div className="drills-list">
                        <h4>Suggested Drills</h4>
                        <ul>
                          {progressionAnalysis.recommendations.suggested_drills.map((drill, i) => (
                            <li key={i}>{drill}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                <div className="progression-meta">
                  <span>Analyzed {progressionAnalysis.sessions_analyzed} sessions</span>
                  {progressionAnalysis.date_range?.from && progressionAnalysis.date_range?.to && (
                    <span>
                      {new Date(progressionAnalysis.date_range.from).toLocaleDateString()} - {new Date(progressionAnalysis.date_range.to).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>
            ) : (
              <div className="progression-error">
                <p>{progressionError || 'Could not load progression analysis'}</p>
                <button className="btn btn-save-confirm" onClick={() => analyzeProgression()} style={{ marginTop: '1rem' }}>
                  Retry Analysis
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Footer */}
      <footer className="footer">
        <p>CARV Analyzer - Powered by Claude AI</p>
        <p className="footer-note">Upload your CARV screenshots to improve your skiing technique</p>
      </footer>
    </div>
  )
}

export default App
