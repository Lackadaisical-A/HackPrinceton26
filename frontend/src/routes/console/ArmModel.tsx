import { useEffect, useRef, useState } from 'react'
import {
  ACESFilmicToneMapping, Color, DirectionalLight, GridHelper, HemisphereLight,
  Mesh, MeshStandardMaterial, PerspectiveCamera, PlaneGeometry, Scene, WebGLRenderer,
} from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import type { URDFRobot } from 'urdf-loader'
import { LIMITS } from '../../config'
import { schedule } from '../../render/scheduler'
import { theme } from '../../render/theme'
import { disposeModel, HOME_POSE, loadSO101, setRobotPose } from '../../robot/so101'
import { useSettings } from '../../state/settings'
import { bus } from '../../telemetry/bus'
import type { RobotMessage } from '../../telemetry/contracts'
import { Button } from '../../ui/Button'

type ViewState = 'loading' | 'ready' | 'error'

/** An observed pose only: orbit/zoom never sends commands to the physical arm. */
export default function ArmModel() {
  const host = useRef<HTMLDivElement>(null)
  const reset = useRef<() => void>(() => {})
  const [state, setState] = useState<ViewState>('loading')
  const [message, setMessage] = useState('Loading SO-101 model…')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const el = host.current!
    const abort = new AbortController()
    let renderer: WebGLRenderer
    try {
      renderer = new WebGLRenderer({ antialias: true, powerPreference: 'low-power' })
    } catch {
      // oxlint-disable-next-line react/set-state-in-effect -- Report the external WebGL capability check.
      setState('error')
      setMessage('3D rendering is unavailable in this browser. Joint readings are still available below.')
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
    renderer.toneMapping = ACESFilmicToneMapping
    renderer.domElement.setAttribute('aria-label', 'SO-101 3D arm. Drag to orbit; scroll or pinch to zoom.')
    renderer.domElement.setAttribute('role', 'img')
    renderer.domElement.style.display = 'block'
    renderer.domElement.style.touchAction = 'none'
    el.appendChild(renderer.domElement)

    // URDF uses Z-up. Keep that coordinate system for the robot and camera.
    const colors = theme()
    const scene = new Scene()
    scene.background = new Color(colors.well)
    const camera = new PerspectiveCamera(38, 1, 0.01, 10)
    camera.up.set(0, 0, 1)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.12
    controls.enablePan = false
    controls.minDistance = 0.35
    controls.maxDistance = 1.8
    controls.maxPolarAngle = Math.PI / 2 + 0.12
    let dirty = true
    const markDirty = () => { dirty = true }
    controls.addEventListener('change', markDirty)
    const homeView = () => {
      // Flush any remaining orbit inertia before restoring an exact camera pose.
      controls.enableDamping = false
      controls.update()
      camera.position.set(0.48, -0.58, 0.40)
      controls.target.set(0, 0, 0.16)
      controls.update()
      controls.enableDamping = true
      dirty = true
    }
    reset.current = homeView
    homeView()

    const sky = new HemisphereLight('#fffaf0', '#65656b', 1.8)
    sky.position.set(0, 0, 1)
    scene.add(sky)
    const key = new DirectionalLight('#ffffff', 2)
    key.position.set(0.4, -0.5, 0.9)
    scene.add(key)
    const fill = new DirectionalLight('#e2eaff', 0.7)
    fill.position.set(-0.5, 0.3, 0.4)
    scene.add(fill)
    const ground = new Mesh(new PlaneGeometry(2, 2), new MeshStandardMaterial({ color: colors.well, roughness: 1 }))
    ground.position.z = -0.003
    scene.add(ground)
    const grid = new GridHelper(1.2, 24, colors.ruleStrong, colors.rule)
    grid.rotation.x = Math.PI / 2
    grid.position.z = -0.002
    scene.add(grid)

    const resize = new ResizeObserver(() => {
      const { width, height } = el.getBoundingClientRect()
      if (width < 1 || height < 1) return
      camera.aspect = width / height
      // Preserve horizontal room for the extended gripper on narrow panels.
      camera.fov = 2 * Math.atan(Math.tan(38 * Math.PI / 360) * Math.max(1, 1.7 / camera.aspect)) * 180 / Math.PI
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
      dirty = true
    })
    resize.observe(el)

    let robot: URDFRobot | null = null
    let previous: RobotMessage | null = null
    let hadData = false
    let priorSource = useSettings.getState().source
    let shown = ''
    const showStatus = (text: string) => {
      if (text !== shown) { shown = text; setMessage(text) }
    }
    const stop = schedule((nowMs) => {
      controls.update()
      const sample = bus.latest('robot')
      const source = useSettings.getState().source
      if (source !== priorSource || (sample === null && hadData)) {
        if (robot) setRobotPose(robot, HOME_POSE)
        priorSource = source
        hadData = false
        previous = null
        dirty = true
      }
      const fresh = sample !== null && bus.ageMs('robot', nowMs) < LIMITS.streamSilentMs
        && Math.abs(Date.now() / 1000 - sample.t) < LIMITS.streamSilentMs / 1000
      if (robot) {
        if (fresh) {
          if (sample !== previous) dirty = setRobotPose(robot, sample.pos) || dirty
          previous = sample
          hadData = true
          showStatus(source === 'simulator' ? 'Simulated pose'
            : sample.control?.feedback === 'preview' ? 'Pot target preview · no robot connected' : 'Live measured pose')
        } else {
          showStatus(hadData ? 'Arm data lost · showing last pose' : 'Waiting for arm data · reference pose')
        }
      }
      // Avoid a continuous GPU load while the camera/model is stationary.
      if (dirty) { renderer.render(scene, camera); dirty = false }
    }, { el, hz: 30 })

    let contextAvailable = true
    const contextLost = (event: Event) => {
      event.preventDefault()
      contextAvailable = false
      stop()
      setState('error')
      setMessage('3D graphics connection lost. Reload the model to restore the view.')
    }
    renderer.domElement.addEventListener('webglcontextlost', contextLost)
    void loadSO101(abort.signal).then((loaded) => {
      if (abort.signal.aborted) { disposeModel(loaded); return }
      robot = loaded
      scene.add(robot)
      dirty = true
      if (contextAvailable) setState('ready')
    }).catch((error: unknown) => {
      if (abort.signal.aborted) return
      console.error('SO-101 model failed to load', error)
      setState('error')
      setMessage('Could not load the SO-101 model. Joint readings are still available below.')
    })

    return () => {
      abort.abort()
      stop()
      resize.disconnect()
      reset.current = () => {}
      controls.removeEventListener('change', markDirty)
      controls.dispose()
      renderer.domElement.removeEventListener('webglcontextlost', contextLost)
      disposeModel(scene)
      grid.geometry.dispose()
      for (const material of Array.isArray(grid.material) ? grid.material : [grid.material]) material.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
    }
  }, [attempt])

  return (
    <div className="mb-3 overflow-hidden rounded-md border border-rule bg-well" data-arm-model={state}>
      <div className="relative">
        <div ref={host} className="h-72 w-full @lg:h-80" />
        {state !== 'ready' && <div className="absolute inset-0 flex items-center justify-center bg-well/90 p-6 text-center text-sm text-ink-2" role="status">
          <div><p>{message}</p>{state === 'error' && <Button className="mt-3" onClick={() => {
            setState('loading')
            setMessage('Loading SO-101 model…')
            setAttempt((value) => value + 1)
          }}>Reload 3D model</Button>}</div>
        </div>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-rule px-3 py-2">
        <div className="text-xs"><p className="text-ink" role="status">{state === 'ready' ? message : 'SO-101 · 3D model'}</p>
          <p className="mt-0.5 text-ink-3">Drag to orbit · scroll or pinch to zoom</p></div>
        <Button size="sm" disabled={state !== 'ready'} onClick={() => reset.current()}>Reset view</Button>
      </div>
    </div>
  )
}
