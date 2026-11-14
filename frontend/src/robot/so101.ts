import { Mesh, type BufferGeometry, type Material, type Object3D } from 'three'
import { STLLoader } from 'three/addons/loaders/STLLoader.js'
import URDFLoader, { type URDFRobot } from 'urdf-loader'
import description from '../../../so101_sim/model/so101_new_calib.urdf?raw'
import { JOINTS } from '../config'

// Vite emits the existing simulation assets into the build, including deployments
// under a subdirectory. No CDN, duplicate model copy, or runtime filesystem route.
const assets = import.meta.glob<string>('../../../so101_sim/model/assets/*.stl', {
  eager: true, query: '?url', import: 'default',
})

export const MODEL_JOINTS = JOINTS.map((joint) => joint.key)
export const HOME_POSE = [0, -20, 35, 0, 0, 35] as const

type JointLimits = Record<string, { lower: number; upper: number }>

/** Telemetry uses degrees for the arm and 0..100 for the gripper. */
export function jointAngles(positions: readonly number[], limits: JointLimits): Record<string, number> {
  if (positions.length !== 6 || positions.some((value) => !Number.isFinite(value))) {
    throw new Error('SO-101 needs six finite joint positions')
  }
  return Object.fromEntries(MODEL_JOINTS.map((name, i) => {
    const { lower, upper } = limits[name]
    const angle = name === 'gripper'
      ? lower + Math.max(0, Math.min(100, positions[i])) / 100 * (upper - lower)
      : positions[i] * Math.PI / 180
    return [name, Math.max(lower, Math.min(upper, angle))]
  }))
}

export function setRobotPose(robot: URDFRobot, positions: readonly number[]): boolean {
  const limits = Object.fromEntries(MODEL_JOINTS.map((name) => [name, robot.joints[name].limit]))
  return robot.setJointValues(jointAngles(positions, limits))
}

export function disposeModel(root: Object3D): void {
  const geometries = new Set<BufferGeometry>()
  const materials = new Set<Material>()
  root.traverse((node) => {
    if (!(node instanceof Mesh)) return
    geometries.add(node.geometry)
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) materials.add(material)
  })
  geometries.forEach((geometry) => geometry.dispose())
  materials.forEach((material) => material.dispose())
}

/** Load all visual meshes before exposing a complete, articulated robot. */
export async function loadSO101(signal: AbortSignal): Promise<URDFRobot> {
  const stl = new STLLoader()
  const results = await Promise.allSettled(Object.entries(assets).map(async ([path, url]) => {
    const response = await fetch(url, { signal })
    if (!response.ok) throw new Error(`Could not load ${path.split('/').pop()}`)
    const bytes = await response.arrayBuffer()
    signal.throwIfAborted()
    return [path.split('/').pop()!, stl.parse(bytes)] as const
  }))
  const meshes = new Map<string, BufferGeometry>()
  for (const result of results) {
    if (result.status === 'fulfilled') meshes.set(...result.value)
  }
  let robot: URDFRobot | undefined
  try {
    signal.throwIfAborted()
    const failure = results.find((result) => result.status === 'rejected')
    if (failure?.status === 'rejected') throw failure.reason
    const loader = new URDFLoader()
    loader.parseCollision = false
    loader.loadMeshCb = (path, _manager, material, done) => {
      const geometry = meshes.get(path.split('/').pop()!)
      if (!geometry) throw new Error(`Missing model mesh: ${path}`)
      done(new Mesh(geometry, material))
    }
    robot = loader.parse(description)
    for (const name of MODEL_JOINTS) {
      if (!robot.joints[name]) throw new Error(`Model is missing joint ${name}`)
    }
    setRobotPose(robot, HOME_POSE)
    return robot
  } catch (error) {
    meshes.forEach((geometry) => geometry.dispose())
    if (robot) disposeModel(robot)
    throw error
  }
}
