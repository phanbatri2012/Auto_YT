import { useEffect, useRef } from 'react'
import './BackgroundCanvas.css'

export const BACKGROUND_MODES = [
  { id: 'particles', label: 'Neural Particles', icon: '🔮' },
  { id: 'aurora', label: 'Aurora Mesh', icon: '🌌' },
  { id: 'grid', label: 'Cyber Grid', icon: '🌐' },
  { id: 'off', label: 'Tối giản (Tắt)', icon: '🌑' }
]

export default function BackgroundCanvas({ mode = 'particles' }) {
  const canvasRef = useRef(null)
  const mouseRef = useRef({ x: -1000, y: -1000, active: false })
  const animFrameRef = useRef(null)

  useEffect(() => {
    if (mode === 'off') return undefined

    const canvas = canvasRef.current
    if (!canvas) return undefined
    const ctx = canvas.getContext('2d')
    if (!ctx) return undefined

    let width = (canvas.width = window.innerWidth)
    let height = (canvas.height = window.innerHeight)
    let dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = width * dpr
    canvas.height = height * dpr
    ctx.scale(dpr, dpr)

    const handleResize = () => {
      if (!canvas) return
      width = window.innerWidth
      height = window.innerHeight
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = width * dpr
      canvas.height = height * dpr
      ctx.scale(dpr, dpr)
      initParticles()
    }

    const handleMouseMove = (e) => {
      mouseRef.current.x = e.clientX
      mouseRef.current.y = e.clientY
      mouseRef.current.active = true
    }

    const handleMouseLeave = () => {
      mouseRef.current.active = false
      mouseRef.current.x = -1000
      mouseRef.current.y = -1000
    }

    window.addEventListener('resize', handleResize, { passive: true })
    window.addEventListener('mousemove', handleMouseMove, { passive: true })
    document.addEventListener('mouseleave', handleMouseLeave, { passive: true })

    // --- Particle System ---
    let particles = []
    const colors = [
      { r: 139, g: 92, b: 246 },  // Violet #8b5cf6
      { r: 99, g: 102, b: 241 },  // Indigo #6366f1
      { r: 6, g: 182, b: 212 },   // Cyan #06b6d4
      { r: 168, g: 85, b: 247 }   // Purple #a855f7
    ]

    const initParticles = () => {
      const count = Math.min(Math.floor((width * height) / 22000), 75)
      particles = []
      for (let i = 0; i < count; i++) {
        const color = colors[Math.floor(Math.random() * colors.length)]
        particles.push({
          x: Math.random() * width,
          y: Math.random() * height,
          vx: (Math.random() - 0.5) * 0.65,
          vy: (Math.random() - 0.5) * 0.65,
          radius: Math.random() * 2 + 1.2,
          baseRadius: Math.random() * 2 + 1.2,
          color,
          alpha: Math.random() * 0.5 + 0.35,
          pulseSpeed: Math.random() * 0.02 + 0.01,
          pulseVal: Math.random() * Math.PI * 2
        })
      }
    }

    // --- Aurora Blobs System ---
    let auroraTime = 0
    const blobs = [
      { xFactor: 0.25, yFactor: 0.3, radius: 380, color: 'rgba(139, 92, 246, 0.14)', speed: 0.0006 },
      { xFactor: 0.75, yFactor: 0.4, radius: 420, color: 'rgba(6, 182, 212, 0.12)', speed: 0.0008 },
      { xFactor: 0.5, yFactor: 0.75, radius: 450, color: 'rgba(99, 102, 241, 0.15)', speed: 0.0005 },
      { xFactor: 0.85, yFactor: 0.8, radius: 350, color: 'rgba(236, 72, 153, 0.1)', speed: 0.0007 }
    ]

    initParticles()

    let isVisible = !document.hidden
    const handleVisibilityChange = () => {
      isVisible = !document.hidden
      if (isVisible) {
        lastTime = performance.now()
        animFrameRef.current = requestAnimationFrame(render)
      }
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)

    let lastTime = performance.now()

    const render = (now) => {
      if (!isVisible) return

      const delta = Math.min((now - lastTime) / 1000, 0.1)
      lastTime = now

      ctx.clearRect(0, 0, width, height)

      if (mode === 'particles') {
        const mouse = mouseRef.current
        const maxDist = 140

        // Draw connections
        for (let i = 0; i < particles.length; i++) {
          const p1 = particles[i]
          for (let j = i + 1; j < particles.length; j++) {
            const p2 = particles[j]
            const dx = p1.x - p2.x
            const dy = p1.y - p2.y
            const dist = Math.hypot(dx, dy)

            if (dist < maxDist) {
              const alpha = (1 - dist / maxDist) * 0.22 * Math.min(p1.alpha, p2.alpha)
              ctx.strokeStyle = `rgba(139, 92, 246, ${alpha})`
              ctx.lineWidth = 1
              ctx.beginPath()
              ctx.moveTo(p1.x, p1.y)
              ctx.lineTo(p2.x, p2.y)
              ctx.stroke()
            }
          }

          // Mouse connection
          if (mouse.active) {
            const mdx = p1.x - mouse.x
            const mdy = p1.y - mouse.y
            const mdist = Math.hypot(mdx, mdy)
            const mouseRadius = 160

            if (mdist < mouseRadius) {
              const malpha = (1 - mdist / mouseRadius) * 0.45
              ctx.strokeStyle = `rgba(6, 182, 212, ${malpha})`
              ctx.lineWidth = 1.2
              ctx.beginPath()
              ctx.moveTo(p1.x, p1.y)
              ctx.lineTo(mouse.x, mouse.y)
              ctx.stroke()

              // Gentle push away
              const force = (1 - mdist / mouseRadius) * 12 * delta
              p1.x += (mdx / (mdist || 1)) * force * 15
              p1.y += (mdy / (mdist || 1)) * force * 15
            }
          }

          // Update position
          p1.x += p1.vx * (delta * 60)
          p1.y += p1.vy * (delta * 60)

          if (p1.x < 0) p1.x = width
          else if (p1.x > width) p1.x = 0
          if (p1.y < 0) p1.y = height
          else if (p1.y > height) p1.y = 0

          // Pulse
          p1.pulseVal += p1.pulseSpeed
          const currentRadius = p1.baseRadius + Math.sin(p1.pulseVal) * 0.6

          // Draw particle
          ctx.fillStyle = `rgba(${p1.color.r}, ${p1.color.g}, ${p1.color.b}, ${p1.alpha})`
          ctx.beginPath()
          ctx.arc(p1.x, p1.y, Math.max(0.5, currentRadius), 0, Math.PI * 2)
          ctx.fill()

          // Subtle glow
          ctx.fillStyle = `rgba(${p1.color.r}, ${p1.color.g}, ${p1.color.b}, ${p1.alpha * 0.25})`
          ctx.beginPath()
          ctx.arc(p1.x, p1.y, currentRadius * 2.8, 0, Math.PI * 2)
          ctx.fill()
        }
      } else if (mode === 'aurora') {
        auroraTime += delta

        blobs.forEach((blob, idx) => {
          const offsetX = Math.sin(auroraTime * 0.6 + idx * 1.5) * (width * 0.15)
          const offsetY = Math.cos(auroraTime * 0.5 + idx * 1.2) * (height * 0.12)
          const cx = width * blob.xFactor + offsetX
          const cy = height * blob.yFactor + offsetY

          const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, blob.radius)
          grad.addColorStop(0, blob.color)
          grad.addColorStop(0.5, blob.color.replace(/[\d.]+\)$/, '0.05)'))
          grad.addColorStop(1, 'rgba(15, 15, 17, 0)')

          ctx.fillStyle = grad
          ctx.beginPath()
          ctx.arc(cx, cy, blob.radius, 0, Math.PI * 2)
          ctx.fill()
        })
      } else if (mode === 'grid') {
        const mouse = mouseRef.current
        const gridSize = 42

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.035)'
        ctx.lineWidth = 1

        ctx.beginPath()
        for (let x = 0; x <= width; x += gridSize) {
          ctx.moveTo(x, 0)
          ctx.lineTo(x, height)
        }
        for (let y = 0; y <= height; y += gridSize) {
          ctx.moveTo(0, y)
          ctx.lineTo(width, y)
        }
        ctx.stroke()

        // Highlight intersection dots
        ctx.fillStyle = 'rgba(139, 92, 246, 0.18)'
        for (let x = 0; x <= width; x += gridSize * 2) {
          for (let y = 0; y <= height; y += gridSize * 2) {
            ctx.beginPath()
            ctx.arc(x, y, 1.5, 0, Math.PI * 2)
            ctx.fill()
          }
        }

        // Mouse Spotlight
        if (mouse.active) {
          const spotGrad = ctx.createRadialGradient(
            mouse.x, mouse.y, 0,
            mouse.x, mouse.y, 240
          )
          spotGrad.addColorStop(0, 'rgba(139, 92, 246, 0.14)')
          spotGrad.addColorStop(0.4, 'rgba(6, 182, 212, 0.06)')
          spotGrad.addColorStop(1, 'rgba(15, 15, 17, 0)')

          ctx.fillStyle = spotGrad
          ctx.beginPath()
          ctx.arc(mouse.x, mouse.y, 240, 0, Math.PI * 2)
          ctx.fill()
        }
      }

      animFrameRef.current = requestAnimationFrame(render)
    }

    animFrameRef.current = requestAnimationFrame(render)

    return () => {
      window.removeEventListener('resize', handleResize)
      window.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseleave', handleMouseLeave)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current)
      }
    }
  }, [mode])

  if (mode === 'off') {
    return null
  }

  return (
    <div className={`nexus-background-layer mode-${mode}`} aria-hidden="true">
      <canvas ref={canvasRef} className="nexus-background-canvas" />
      <div className="nexus-background-overlay" />
    </div>
  )
}
