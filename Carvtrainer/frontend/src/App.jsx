import { useState, useRef, useEffect } from 'react'
import axios from 'axios'
import './App.css'

// Storage key for progress logs
const STORAGE_KEY = 'carv_progress_logs'

function App() {
  // State management
  const [selectedFiles, setSelectedFiles] = useState([]) // Array of {file, preview, id, name}
  const [analysis, setAnalysis] = useState(null) // Holistic analysis result
  const [trainingPlan, setTrainingPlan] = useState(null)
  const [loading, setLoading] = useState(false)
  const [planLoading, setPlanLoading] = useState(false)
  const [error, setError] = useState(null)
  const fileInputRef = useRef(null)

  // Progress logging state
  const [progressLogs, setProgressLogs] = useState([])
  const [showProgressPanel, setShowProgressPanel] = useState(false)
  const [sessionDateTime, setSessionDateTime] = useState('')
  const [sessionNotes, setSessionNotes] = useState('')
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [selectedLogForComparison, setSelectedLogForComparison] = useState(null)

  // Load progress logs from localStorage on mount
  useEffect(() => {
    const savedLogs = localStorage.getItem(STORAGE_KEY)
    if (savedLogs) {
      try {
        setProgressLogs(JSON.parse(savedLogs))
      } catch (e) {
        console.error('Failed to load progress logs:', e)
      }
    }
  }, [])

  // Save progress logs to localStorage whenever they change
  useEffect(() => {
    if (progressLogs.length > 0) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(progressLogs))
    }
  }, [progressLogs])

  // Extract metadata from files when selected
  const extractMetadataFromFiles = async (files) => {
    try {
      const formData = new FormData()
      files.forEach(fileObj => {
        formData.append('images', fileObj.file)
      })

      const response = await axios.post('/api/extract-metadata', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })

      // Find the first datetime from metadata
      if (response.data.metadata) {
        for (const meta of response.data.metadata) {
          if (meta.datetime) {
            // Format datetime for input field
            const dt = new Date(meta.datetime)
            const formatted = dt.toISOString().slice(0, 16) // Format: YYYY-MM-DDTHH:MM
            setSessionDateTime(formatted)
            return
          }
        }
      }
      // If no datetime found, use current time
      setSessionDateTime(new Date().toISOString().slice(0, 16))
    } catch (e) {
      console.error('Failed to extract metadata:', e)
      setSessionDateTime(new Date().toISOString().slice(0, 16))
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

  // Analyze all screenshots holistically (sends all to Claude at once)
  const analyzeAllScreenshots = async () => {
    if (selectedFiles.length === 0) {
      setError('Please select at least one screenshot')
      return
    }

    setLoading(true)
    setError(null)
    setAnalysis(null)
    setTrainingPlan(null)

    try {
      // Send all images in a single request
      const formData = new FormData()
      selectedFiles.forEach(fileObj => {
        formData.append('images', fileObj.file)
      })

      const response = await axios.post('/api/analyze', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      })

      setAnalysis(response.data)

      // Update sessionDateTime from CARV screenshot if available (master timestamp)
      if (response.data?.session_overview?.session_datetime) {
        const carvDateTime = response.data.session_overview.session_datetime
        // Format for datetime-local input (YYYY-MM-DDTHH:MM)
        setSessionDateTime(carvDateTime.slice(0, 16))
      }
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
      const response = await axios.post('/api/generate-plan', analysis)
      setTrainingPlan(response.data.training_plan)
    } catch (err) {
      const errorMessage = err.response?.data?.message ||
                          err.response?.data?.error ||
                          'Failed to generate training plan'
      setError(errorMessage)
    } finally {
      setPlanLoading(false)
    }
  }

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

  // Parse training plan into structured sections
  const parseTrainingPlan = (text) => {
    if (!text) return null

    const sections = {
      title: '',
      bigPicture: [],
      immediateFocus: { drill: '', cue: '', details: [] },
      drills: [], // NEW: 3 key drills
      dailyPlan: [], // NEW: daily session plan
      weeklySchedule: [], // NEW: weekly training schedule
      weekPlan: [],
      strengths: [],
      progression: { week1_2: [], week3_4: [] },
      sessionPlan: [],
      checkpoints: [],
      mentalCues: [],
      traps: [],
      rawSections: {}
    }

    // Split by main headers
    const lines = text.split('\n')
    let currentSection = ''
    let currentContent = []
    let currentPriority = null

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i]

      // Main title
      if (line.startsWith('# ')) {
        sections.title = line.replace('# ', '').trim()
        continue
      }

      // Section headers
      if (line.startsWith('## ')) {
        // Save previous section
        if (currentSection) {
          sections.rawSections[currentSection] = currentContent.join('\n')
        }
        currentSection = line.replace('## ', '').trim()
        currentContent = []
        continue
      }

      // Sub-section headers
      if (line.startsWith('### ')) {
        currentContent.push(line)
        continue
      }

      currentContent.push(line)
    }

    // Save last section
    if (currentSection) {
      sections.rawSections[currentSection] = currentContent.join('\n')
    }

    // Parse specific sections
    Object.keys(sections.rawSections).forEach(key => {
      const content = sections.rawSections[key]
      const keyLower = key.toLowerCase()

      if (keyLower.includes('big picture')) {
        sections.bigPicture = content.split('\n').filter(l => l.trim().startsWith('-')).map(l => l.replace(/^-\s*/, '').trim())
      }

      if (keyLower.includes('immediate focus')) {
        const lines = content.split('\n').filter(l => l.trim())
        sections.immediateFocus.details = lines.map(l => {
          if (l.includes('Mental cue') || l.includes('mental cue') || l.includes('Cue:')) {
            sections.immediateFocus.cue = l.replace(/.*?[:"]\s*/, '').replace(/[""]/g, '').trim()
          }
          return l.replace(/^-\s*/, '').replace(/^\*\*.*?\*\*:?\s*/, '').trim()
        }).filter(l => l && !l.startsWith('#'))
      }

      if (keyLower.includes("week's game plan") || keyLower.includes('week plan') || keyLower.includes('priorities')) {
        // Parse priority blocks
        const priorityBlocks = content.split(/###\s*Priority\s*\d+/i).filter(b => b.trim())
        sections.weekPlan = priorityBlocks.map(block => {
          const lines = block.split('\n').filter(l => l.trim())
          const priority = {
            name: '',
            current: '',
            target: '',
            drill: '',
            terrain: '',
            details: []
          }
          lines.forEach(line => {
            if (line.includes('Current:') && line.includes('→')) {
              const match = line.match(/Current:\s*(\d+).*?→.*?(\d+)/i)
              if (match) {
                priority.current = match[1]
                priority.target = match[2]
              }
            }
            if (line.includes(':')) {
              const [label, value] = line.split(':').map(s => s.trim())
              if (label.toLowerCase().includes('drill')) priority.drill = value.replace(/\*\*/g, '')
              if (label.toLowerCase().includes('terrain')) priority.terrain = value.replace(/\*\*/g, '')
              if (!priority.name && line.startsWith('###')) {
                priority.name = line.replace('###', '').replace(/\[.*?\]/g, '').trim()
              }
            }
            priority.details.push(line.replace(/^-\s*/, '').replace(/^\*\*.*?\*\*:?\s*/, '').trim())
          })
          if (!priority.name && lines[0]) priority.name = lines[0].replace(/[:\[\]#*]/g, '').trim()
          return priority
        }).filter(p => p.name || p.drill)
      }

      if (keyLower.includes('building on strength')) {
        sections.strengths = content.split('\n').filter(l => l.trim().startsWith('-')).map(l => l.replace(/^-\s*/, '').trim())
      }

      // Parse 3 Key Drills
      if (keyLower.includes('3 key drills') || keyLower.includes('your 3 key drills')) {
        const drillBlocks = content.split(/###\s*Drill\s*\d+/i).filter(b => b.trim())
        sections.drills = drillBlocks.map(block => {
          const lines = block.split('\n').filter(l => l.trim())
          const drill = {
            name: '',
            focus: '',
            targetMetric: '',
            why: '',
            execution: '',
            runsPerSession: '',
            turnsPerRun: '',
            terrain: '',
            successFeels: '',
            commonMistake: '',
            progression: ''
          }
          lines.forEach(line => {
            const cleanLine = line.replace(/^-\s*/, '').replace(/\*\*/g, '')
            if (line.includes(':')) {
              const colonIdx = cleanLine.indexOf(':')
              const label = cleanLine.substring(0, colonIdx).toLowerCase()
              const value = cleanLine.substring(colonIdx + 1).trim()

              if (label.includes('target metric')) drill.targetMetric = value
              if (label.includes('why this drill')) drill.why = value
              if (label.includes('execution')) drill.execution = value
              if (label.includes('runs per session')) drill.runsPerSession = value
              if (label.includes('turns per run')) drill.turnsPerRun = value
              if (label.includes('terrain')) drill.terrain = value
              if (label.includes('success feels')) drill.successFeels = value
              if (label.includes('common mistake')) drill.commonMistake = value
              if (label.includes('progression')) drill.progression = value
            }
            // Get drill name from first line or header remnant
            if (!drill.name && lines.indexOf(line) === 0) {
              drill.name = cleanLine.replace(/[:\-–]/g, ' ').replace(/Primary Focus|Secondary Focus|Integration|Refinement/gi, '').trim()
              const focusMatch = line.match(/(Primary Focus|Secondary Focus|Integration|Refinement)/i)
              if (focusMatch) drill.focus = focusMatch[1]
            }
          })
          return drill
        }).filter(d => d.name || d.execution)
      }

      // Parse Daily Session Plan
      if (keyLower.includes('daily session plan') || keyLower.includes('daily plan')) {
        const runBlocks = content.split(/\*\*Run\s*[\d-]+/i).filter(b => b.trim())
        sections.dailyPlan = []
        const runMatches = content.match(/\*\*Run\s*[\d-]+[^*]*\*\*/gi) || []
        runMatches.forEach((runHeader, idx) => {
          const runMatch = runHeader.match(/Run\s*([\d-]+)/i)
          const phaseMatch = runHeader.match(/:\s*([^*]+)/i)
          const blockContent = runBlocks[idx + 1] || ''
          const details = blockContent.split('\n').filter(l => l.trim().startsWith('-')).map(l => l.replace(/^-\s*/, '').trim())

          sections.dailyPlan.push({
            runs: runMatch ? runMatch[1] : '',
            phase: phaseMatch ? phaseMatch[1].trim() : '',
            details: details
          })
        })
      }

      // Parse Weekly Training Schedule
      if (keyLower.includes('weekly training schedule') || keyLower.includes('weekly schedule')) {
        const dayBlocks = content.split(/###\s*Day\s*\d+/i).filter(b => b.trim())
        sections.weeklySchedule = dayBlocks.map((block, idx) => {
          const lines = block.split('\n').filter(l => l.trim())
          const day = {
            dayNum: idx + 1,
            name: '',
            primaryFocus: '',
            secondary: '',
            terrain: '',
            goal: '',
            details: []
          }
          lines.forEach(line => {
            const cleanLine = line.replace(/^-\s*/, '').replace(/\*\*/g, '')
            if (line.includes(':')) {
              const colonIdx = cleanLine.indexOf(':')
              const label = cleanLine.substring(0, colonIdx).toLowerCase()
              const value = cleanLine.substring(colonIdx + 1).trim()

              if (label.includes('primary focus')) day.primaryFocus = value
              if (label.includes('secondary')) day.secondary = value
              if (label.includes('terrain')) day.terrain = value
              if (label.includes('goal')) day.goal = value
              if (label.includes('add')) day.details.push('Add: ' + value)
            }
            // Get day name from first line
            if (!day.name && lines.indexOf(line) === 0) {
              day.name = cleanLine.replace(/[:\-–]/g, ' ').trim()
            }
          })
          return day
        }).filter(d => d.name || d.primaryFocus)
      }

      if (keyLower.includes('progression') || keyLower.includes('4-week')) {
        const week1Match = content.match(/###\s*Week\s*1[-–]2[\s\S]*?(?=###|$)/i)
        const week3Match = content.match(/###\s*Week\s*3[-–]4[\s\S]*?(?=###|$)/i)
        if (week1Match) {
          sections.progression.week1_2 = week1Match[0].split('\n').filter(l => l.trim().startsWith('-')).map(l => l.replace(/^-\s*/, '').trim())
        }
        if (week3Match) {
          sections.progression.week3_4 = week3Match[0].split('\n').filter(l => l.trim().startsWith('-')).map(l => l.replace(/^-\s*/, '').trim())
        }
      }

      if (keyLower.includes('session plan') || keyLower.includes('sample session')) {
        sections.sessionPlan = content.split('\n').filter(l => l.trim().startsWith('**') || l.trim().startsWith('Run')).map(l => {
          const match = l.match(/\*\*(Run\s*[\d-]+)\*\*:?\s*(.*)/i) || l.match(/(Run\s*[\d-]+):?\s*(.*)/i)
          if (match) {
            return { run: match[1], activity: match[2].replace(/\*\*/g, '') }
          }
          return { run: '', activity: l.replace(/\*\*/g, '').trim() }
        }).filter(s => s.activity)
      }

      if (keyLower.includes('checkpoint') || keyLower.includes('progress')) {
        sections.checkpoints = content.split('\n').filter(l => l.trim().startsWith('-')).map(l => l.replace(/^-\s*/, '').trim())
      }

      if (keyLower.includes('mental cue')) {
        sections.mentalCues = content.split('\n').filter(l => l.trim().startsWith('-')).map(l => l.replace(/^-\s*/, '').trim())
      }

      if (keyLower.includes('trap') || keyLower.includes('avoid')) {
        sections.traps = content.split('\n').filter(l => l.trim().startsWith('-')).map(l => l.replace(/^-\s*/, '').trim())
      }
    })

    return sections
  }

  // Render structured training plan
  const renderTrainingPlan = (planText) => {
    const plan = parseTrainingPlan(planText)
    if (!plan) return null

    return (
      <div className="training-plan-structured">
        {/* Plan Header */}
        {plan.title && (
          <div className="plan-header">
            <div className="plan-header-icon">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
                <line x1="10" y1="9" x2="8" y2="9"/>
              </svg>
            </div>
            <h1>{plan.title}</h1>
          </div>
        )}

        {/* Big Picture Summary */}
        {plan.bigPicture.length > 0 && (
          <div className="plan-section big-picture-section">
            <div className="section-header">
              <span className="section-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="11" cy="11" r="8"/>
                  <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                  <circle cx="11" cy="11" r="3"/>
                </svg>
              </span>
              <h2>The Big Picture</h2>
            </div>
            <div className="big-picture-content">
              {plan.bigPicture.map((item, idx) => (
                <p key={idx}>{item}</p>
              ))}
            </div>
          </div>
        )}

        {/* Immediate Focus - Hero Card */}
        {plan.immediateFocus.details.length > 0 && (
          <div className="plan-section immediate-focus-section">
            <div className="section-header">
              <span className="section-icon focus-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10"/>
                  <circle cx="12" cy="12" r="6"/>
                  <circle cx="12" cy="12" r="2"/>
                </svg>
              </span>
              <h2>Immediate Focus</h2>
              <span className="section-badge">Next 1-3 Runs</span>
            </div>
            <div className="focus-card">
              {plan.immediateFocus.cue && (
                <div className="mental-cue-hero">
                  <span className="cue-label">Your Mantra</span>
                  <span className="cue-text">"{plan.immediateFocus.cue}"</span>
                </div>
              )}
              <ul className="focus-details">
                {plan.immediateFocus.details.slice(0, 5).map((detail, idx) => (
                  <li key={idx}>{detail}</li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {/* 3 Key Drills */}
        {plan.drills.length > 0 && (
          <div className="plan-section drills-section">
            <div className="section-header">
              <span className="section-icon drills-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                </svg>
              </span>
              <h2>Your 3 Key Drills</h2>
              <span className="section-badge drill-badge">Master These</span>
            </div>
            <div className="drills-grid">
              {plan.drills.map((drill, idx) => (
                <div key={idx} className={`drill-card drill-${idx + 1}`}>
                  <div className="drill-header">
                    <span className="drill-number">{idx + 1}</span>
                    <div className="drill-title">
                      <h3>{drill.name}</h3>
                      {drill.focus && <span className="drill-focus-tag">{drill.focus}</span>}
                    </div>
                  </div>
                  {drill.targetMetric && (
                    <div className="drill-metric">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="12" cy="12" r="10"/>
                        <circle cx="12" cy="12" r="3"/>
                      </svg>
                      <span>Target: {drill.targetMetric}</span>
                    </div>
                  )}
                  {drill.why && (
                    <p className="drill-why">{drill.why}</p>
                  )}
                  {drill.execution && (
                    <div className="drill-execution">
                      <strong>How to do it:</strong>
                      <p>{drill.execution}</p>
                    </div>
                  )}
                  <div className="drill-stats">
                    {drill.runsPerSession && (
                      <div className="drill-stat">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                          <circle cx="9" cy="7" r="4"/>
                        </svg>
                        <span>{drill.runsPerSession}</span>
                      </div>
                    )}
                    {drill.turnsPerRun && (
                      <div className="drill-stat">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <polyline points="23 4 23 10 17 10"/>
                          <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                        </svg>
                        <span>{drill.turnsPerRun}</span>
                      </div>
                    )}
                    {drill.terrain && (
                      <div className="drill-stat terrain">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M3 17l6-6 4 4 8-8"/>
                        </svg>
                        <span>{drill.terrain}</span>
                      </div>
                    )}
                  </div>
                  {drill.successFeels && (
                    <div className="drill-success">
                      <span className="success-label">Success feels like:</span>
                      <span className="success-text">"{drill.successFeels}"</span>
                    </div>
                  )}
                  {drill.commonMistake && (
                    <div className="drill-warning">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="12" cy="12" r="10"/>
                        <line x1="12" y1="8" x2="12" y2="12"/>
                        <line x1="12" y1="16" x2="12.01" y2="16"/>
                      </svg>
                      <span>Watch out: {drill.commonMistake}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Daily Session Plan */}
        {plan.dailyPlan.length > 0 && (
          <div className="plan-section daily-plan-section">
            <div className="section-header">
              <span className="section-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10"/>
                  <polyline points="12 6 12 12 16 14"/>
                </svg>
              </span>
              <h2>Daily Session Plan</h2>
              <span className="section-badge">10 Runs</span>
            </div>
            <div className="daily-timeline">
              {plan.dailyPlan.map((phase, idx) => (
                <div key={idx} className={`daily-phase phase-${idx + 1}`}>
                  <div className="phase-marker">
                    <div className="phase-dot"></div>
                    {idx < plan.dailyPlan.length - 1 && <div className="phase-line"></div>}
                  </div>
                  <div className="phase-content">
                    <div className="phase-header">
                      <span className="phase-runs">Run {phase.runs}</span>
                      <span className="phase-name">{phase.phase}</span>
                    </div>
                    {phase.details.length > 0 && (
                      <ul className="phase-details">
                        {phase.details.map((detail, dIdx) => (
                          <li key={dIdx}>{detail}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Weekly Training Schedule */}
        {plan.weeklySchedule.length > 0 && (
          <div className="plan-section weekly-schedule-section">
            <div className="section-header">
              <span className="section-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
                  <line x1="16" y1="2" x2="16" y2="6"/>
                  <line x1="8" y1="2" x2="8" y2="6"/>
                  <line x1="3" y1="10" x2="21" y2="10"/>
                </svg>
              </span>
              <h2>Weekly Training Schedule</h2>
            </div>
            <div className="weekly-grid">
              {plan.weeklySchedule.map((day, idx) => (
                <div key={idx} className={`day-card day-${idx + 1}`}>
                  <div className="day-header">
                    <span className="day-number">Day {day.dayNum}</span>
                    <span className="day-name">{day.name}</span>
                  </div>
                  <div className="day-content">
                    {day.primaryFocus && (
                      <div className="day-focus primary">
                        <span className="focus-label">Primary:</span>
                        <span className="focus-value">{day.primaryFocus}</span>
                      </div>
                    )}
                    {day.secondary && (
                      <div className="day-focus secondary">
                        <span className="focus-label">Secondary:</span>
                        <span className="focus-value">{day.secondary}</span>
                      </div>
                    )}
                    {day.terrain && (
                      <div className="day-terrain">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M3 17l6-6 4 4 8-8"/>
                        </svg>
                        {day.terrain}
                      </div>
                    )}
                    {day.goal && (
                      <div className="day-goal">
                        <span className="goal-label">Goal:</span>
                        <span className="goal-value">{day.goal}</span>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Weekly Game Plan - Priorities */}
        {plan.weekPlan.length > 0 && (
          <div className="plan-section week-plan-section">
            <div className="section-header">
              <span className="section-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                  <polyline points="22 4 12 14.01 9 11.01"/>
                </svg>
              </span>
              <h2>This Week's Priorities</h2>
            </div>
            <div className="priorities-cards">
              {plan.weekPlan.map((priority, idx) => (
                <div key={idx} className={`priority-card priority-${idx + 1}`}>
                  <div className="priority-badge">Priority {idx + 1}</div>
                  <h3>{priority.name}</h3>
                  {(priority.current || priority.target) && (
                    <div className="score-progression">
                      <span className="current-score">{priority.current}</span>
                      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="5" y1="12" x2="19" y2="12"/>
                        <polyline points="12 5 19 12 12 19"/>
                      </svg>
                      <span className="target-score">{priority.target}</span>
                    </div>
                  )}
                  {priority.drill && (
                    <div className="drill-tag">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                      </svg>
                      {priority.drill}
                    </div>
                  )}
                  {priority.terrain && (
                    <div className="terrain-tag">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                        <circle cx="9" cy="7" r="4"/>
                        <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
                        <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                      </svg>
                      {priority.terrain}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Session Structure */}
        {plan.sessionPlan.length > 0 && (
          <div className="plan-section session-plan-section">
            <div className="section-header">
              <span className="section-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                </svg>
              </span>
              <h2>Session Structure</h2>
            </div>
            <div className="session-timeline">
              {plan.sessionPlan.map((session, idx) => (
                <div key={idx} className="session-item">
                  <div className="session-marker">
                    <div className="marker-dot"></div>
                    {idx < plan.sessionPlan.length - 1 && <div className="marker-line"></div>}
                  </div>
                  <div className="session-content">
                    <span className="session-run">{session.run}</span>
                    <span className="session-activity">{session.activity}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 4-Week Progression */}
        {(plan.progression.week1_2.length > 0 || plan.progression.week3_4.length > 0) && (
          <div className="plan-section progression-section">
            <div className="section-header">
              <span className="section-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="12" y1="20" x2="12" y2="10"/>
                  <line x1="18" y1="20" x2="18" y2="4"/>
                  <line x1="6" y1="20" x2="6" y2="16"/>
                </svg>
              </span>
              <h2>4-Week Progression</h2>
            </div>
            <div className="progression-grid">
              {plan.progression.week1_2.length > 0 && (
                <div className="progression-phase">
                  <div className="phase-header">
                    <span className="phase-icon">1-2</span>
                    <h3>Foundation</h3>
                  </div>
                  <ul>
                    {plan.progression.week1_2.map((item, idx) => (
                      <li key={idx}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}
              {plan.progression.week3_4.length > 0 && (
                <div className="progression-phase phase-advanced">
                  <div className="phase-header">
                    <span className="phase-icon">3-4</span>
                    <h3>Integration</h3>
                  </div>
                  <ul>
                    {plan.progression.week3_4.map((item, idx) => (
                      <li key={idx}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Progress Checkpoints */}
        {plan.checkpoints.length > 0 && (
          <div className="plan-section checkpoints-section">
            <div className="section-header">
              <span className="section-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                  <polyline points="22 4 12 14.01 9 11.01"/>
                </svg>
              </span>
              <h2>Progress Checkpoints</h2>
            </div>
            <div className="checkpoints-list">
              {plan.checkpoints.map((checkpoint, idx) => (
                <div key={idx} className="checkpoint-item">
                  <div className="checkpoint-icon">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="9 11 12 14 22 4"/>
                      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
                    </svg>
                  </div>
                  <span>{checkpoint}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Mental Cues */}
        {plan.mentalCues.length > 0 && (
          <div className="plan-section cues-section">
            <div className="section-header">
              <span className="section-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
                  <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
                  <line x1="12" y1="22.08" x2="12" y2="12"/>
                </svg>
              </span>
              <h2>Mental Cues</h2>
            </div>
            <div className="cues-grid">
              {plan.mentalCues.map((cue, idx) => (
                <div key={idx} className="cue-card">
                  <span className="cue-quote">"</span>
                  <p>{cue.replace(/["]/g, '').replace(/^(Primary|Transition|Confidence)\s*Cue:\s*/i, '')}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Traps to Avoid */}
        {plan.traps.length > 0 && (
          <div className="plan-section traps-section">
            <div className="section-header">
              <span className="section-icon warning-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                  <line x1="12" y1="9" x2="12" y2="13"/>
                  <line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
              </span>
              <h2>Traps to Avoid</h2>
            </div>
            <div className="traps-list">
              {plan.traps.map((trap, idx) => (
                <div key={idx} className="trap-item">
                  <span className="trap-icon">!</span>
                  <span>{trap}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Motivational Footer */}
        <div className="plan-footer">
          <div className="footer-quote">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
            </svg>
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

  // Save current session to progress log
  const saveToProgressLog = () => {
    if (!analysis) return

    // Use session_datetime from CARV screenshot as master timestamp (priority 1)
    // Fall back to sessionDateTime (from EXIF/filename) (priority 2)
    // Finally fall back to current time (priority 3)
    const masterDateTime = analysis?.session_overview?.session_datetime ||
      sessionDateTime ||
      new Date().toISOString()

    const logEntry = {
      id: `log-${Date.now()}`,
      datetime: masterDateTime,
      datetimeDisplay: analysis?.session_overview?.session_date_display || null,
      datetimeSource: analysis?.session_overview?.session_datetime ? 'carv_screenshot' :
        (sessionDateTime ? 'exif_or_filename' : 'current_time'),
      notes: sessionNotes,
      savedAt: new Date().toISOString(),
      analysis: analysis,
      trainingPlan: trainingPlan,
      screenshotPreviews: selectedFiles.map(f => f.preview).slice(0, 3), // Save up to 3 previews
      metrics: {
        skiIQ: skiIQ,
        balance: analysis.overall_metrics?.balance,
        edging: analysis.overall_metrics?.edging,
        rotary: analysis.overall_metrics?.rotary,
        performance: analysis.overall_metrics?.performance
      }
    }

    setProgressLogs(prev => [...prev, logEntry])
    setShowSaveDialog(false)
    setSessionNotes('')
  }

  // Delete a progress log entry
  const deleteProgressLog = (logId) => {
    setProgressLogs(prev => prev.filter(log => log.id !== logId))
    if (selectedLogForComparison?.id === logId) {
      setSelectedLogForComparison(null)
    }
    // Update localStorage
    const updated = progressLogs.filter(log => log.id !== logId)
    if (updated.length === 0) {
      localStorage.removeItem(STORAGE_KEY)
    }
  }

  // Load a previous log for viewing
  const loadProgressLog = (log) => {
    setAnalysis(log.analysis)
    setTrainingPlan(log.trainingPlan)
    setSessionDateTime(log.datetime)
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

  // Get sorted logs (newest first)
  const sortedLogs = [...progressLogs].sort((a, b) =>
    new Date(b.datetime) - new Date(a.datetime)
  )

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
          onClick={() => setShowProgressPanel(!showProgressPanel)}
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

            {/* Metrics Grid */}
            {analysis.overall_metrics && (
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
              <button className="btn btn-cancel" onClick={() => setShowSaveDialog(false)}>Cancel</button>
              <button className="btn btn-save-confirm" onClick={saveToProgressLog}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
                Save Session
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
              <button className="close-btn" onClick={() => setShowProgressPanel(false)}>×</button>
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
              <>
                {/* Comparison Selection */}
                {sortedLogs.length > 1 && selectedLogForComparison && (
                  <div className="comparison-banner">
                    <span>Comparing with: {formatDateTime(selectedLogForComparison.datetime, selectedLogForComparison.datetimeDisplay)}</span>
                    <button onClick={() => setSelectedLogForComparison(null)}>Clear</button>
                  </div>
                )}

                {/* Progress Chart - Simple Ski:IQ over time */}
                {sortedLogs.length > 1 && (
                  <div className="progress-chart-container">
                    <h3>Ski:IQ Progress</h3>
                    <div className="mini-chart">
                      {[...sortedLogs].reverse().map((log, idx) => {
                        const maxIQ = Math.max(...sortedLogs.map(l => l.metrics.skiIQ || 100))
                        const minIQ = Math.min(...sortedLogs.map(l => l.metrics.skiIQ || 100))
                        const range = maxIQ - minIQ || 20
                        const height = log.metrics.skiIQ
                          ? ((log.metrics.skiIQ - minIQ + 10) / (range + 20)) * 100
                          : 50
                        return (
                          <div key={log.id} className="chart-bar-container">
                            <div
                              className="chart-bar"
                              style={{ height: `${height}%` }}
                              title={`${formatDateTime(log.datetime, log.datetimeDisplay)}: ${log.metrics.skiIQ ? Math.round(log.metrics.skiIQ) : 'N/A'}`}
                            >
                              <span className="bar-value">{log.metrics.skiIQ ? Math.round(log.metrics.skiIQ) : '?'}</span>
                            </div>
                            <span className="bar-date">{new Date(log.datetime).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* Logs List */}
                <div className="logs-list">
                  {sortedLogs.map((log, idx) => {
                    const prevLog = sortedLogs[idx + 1] // Previous chronological log
                    const skiIQChange = prevLog ? getMetricChange(log.metrics.skiIQ, prevLog.metrics.skiIQ) : null

                    return (
                      <div key={log.id} className="log-card">
                        <div className="log-card-header">
                          <div className="log-date">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
                              <line x1="16" y1="2" x2="16" y2="6"/>
                              <line x1="8" y1="2" x2="8" y2="6"/>
                              <line x1="3" y1="10" x2="21" y2="10"/>
                            </svg>
                            {formatDateTime(log.datetime, log.datetimeDisplay)}
                          </div>
                          <div className="log-actions">
                            {sortedLogs.length > 1 && (
                              <button
                                className={`compare-btn ${selectedLogForComparison?.id === log.id ? 'active' : ''}`}
                                onClick={() => setSelectedLogForComparison(selectedLogForComparison?.id === log.id ? null : log)}
                                title="Compare with other sessions"
                              >
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <line x1="18" y1="20" x2="18" y2="10"/>
                                  <line x1="12" y1="20" x2="12" y2="4"/>
                                  <line x1="6" y1="20" x2="6" y2="14"/>
                                </svg>
                              </button>
                            )}
                            <button className="delete-btn" onClick={() => deleteProgressLog(log.id)} title="Delete this session">
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <polyline points="3 6 5 6 21 6"/>
                                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                                <path d="M10 11v6"/>
                                <path d="M14 11v6"/>
                              </svg>
                            </button>
                          </div>
                        </div>

                        {/* Thumbnails */}
                        {log.screenshotPreviews?.length > 0 && (
                          <div className="log-thumbnails">
                            {log.screenshotPreviews.map((preview, i) => (
                              <img key={i} src={preview} alt={`Screenshot ${i + 1}`} />
                            ))}
                          </div>
                        )}

                        {/* Metrics Summary */}
                        <div className="log-metrics">
                          <div className="log-metric-item main">
                            <span className="metric-label">Ski:IQ</span>
                            <span className="metric-value">{log.metrics.skiIQ ? Math.round(log.metrics.skiIQ) : 'N/A'}</span>
                            {skiIQChange !== null && (
                              <span className={`metric-change ${skiIQChange >= 0 ? 'positive' : 'negative'}`}>
                                {skiIQChange >= 0 ? '+' : ''}{Math.round(skiIQChange)}
                              </span>
                            )}
                          </div>
                          <div className="log-metric-grid">
                            <div className="log-metric-item">
                              <span className="metric-label">Balance</span>
                              <span className="metric-value">{log.metrics.balance?.category_average ? Math.round(log.metrics.balance.category_average) : 'N/A'}</span>
                            </div>
                            <div className="log-metric-item">
                              <span className="metric-label">Edging</span>
                              <span className="metric-value">{log.metrics.edging?.category_average ? Math.round(log.metrics.edging.category_average) : 'N/A'}</span>
                            </div>
                            <div className="log-metric-item">
                              <span className="metric-label">Rotary</span>
                              <span className="metric-value">{log.metrics.rotary?.category_average ? Math.round(log.metrics.rotary.category_average) : 'N/A'}</span>
                            </div>
                            <div className="log-metric-item">
                              <span className="metric-label">G-Force</span>
                              <span className="metric-value">{log.metrics.performance?.turn_g_force ? Math.round(log.metrics.performance.turn_g_force) : 'N/A'}</span>
                            </div>
                          </div>
                        </div>

                        {/* Notes */}
                        {log.notes && (
                          <div className="log-notes">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                              <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                            </svg>
                            {log.notes}
                          </div>
                        )}

                        {/* Load Button */}
                        <button className="load-log-btn" onClick={() => loadProgressLog(log)}>
                          View Full Analysis
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="5" y1="12" x2="19" y2="12"/>
                            <polyline points="12 5 19 12 12 19"/>
                          </svg>
                        </button>
                      </div>
                    )
                  })}
                </div>
              </>
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
