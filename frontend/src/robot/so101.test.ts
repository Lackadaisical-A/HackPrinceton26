import { describe, expect, it } from 'vitest'
import { jointAngles } from './so101'

const limits = {
  shoulder_pan: { lower: -1.91986, upper: 1.91986 },
  shoulder_lift: { lower: -1.74533, upper: 1.74533 },
  elbow_flex: { lower: -1.69, upper: 1.69 },
  wrist_flex: { lower: -1.65806, upper: 1.65806 },
  wrist_roll: { lower: -2.74385, upper: 2.84121 },
  gripper: { lower: -0.174533, upper: 1.74533 },
}

describe('SO-101 telemetry to URDF', () => {
  it('converts measured degrees in the canonical joint order, preserving direction', () => {
    const result = jointAngles([90, -45, 30, -60, 120, 50], limits)
    expect(result.shoulder_pan).toBeCloseTo(Math.PI / 2)
    expect(result.shoulder_lift).toBeCloseTo(-Math.PI / 4)
    expect(result.elbow_flex).toBeCloseTo(Math.PI / 6)
    expect(result.wrist_flex).toBeCloseTo(-Math.PI / 3)
    expect(result.wrist_roll).toBeCloseTo(2 * Math.PI / 3)
    expect(result.gripper).toBeCloseTo((limits.gripper.lower + limits.gripper.upper) / 2)
  })
  it('maps gripper percent to URDF travel rather than treating it as degrees', () => {
    expect(jointAngles([0, 0, 0, 0, 0, 0], limits).gripper).toBe(limits.gripper.lower)
    expect(jointAngles([0, 0, 0, 0, 0, 100], limits).gripper).toBeCloseTo(limits.gripper.upper)
  })
  it('bounds the visual model by its URDF joint limits', () => {
    const result = jointAngles([300, -300, 300, -300, 300, 150], limits)
    expect(result.shoulder_pan).toBe(limits.shoulder_pan.upper)
    expect(result.shoulder_lift).toBe(limits.shoulder_lift.lower)
    expect(result.elbow_flex).toBe(limits.elbow_flex.upper)
    expect(result.wrist_flex).toBe(limits.wrist_flex.lower)
    expect(result.wrist_roll).toBe(limits.wrist_roll.upper)
    expect(result.gripper).toBeCloseTo(limits.gripper.upper)
  })
  it('rejects missing or invalid positions without partially changing the model', () => {
    for (const pos of [[1, 2], [0, 0, 0, 0, NaN, 0], [0, 0, 0, 0, Infinity, 0]]) {
      expect(() => jointAngles(pos, limits)).toThrow('six finite joint positions')
    }
  })
})
