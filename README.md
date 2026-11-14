# PAUL

> **Move the arm. Feel the interaction.**

PAUL is an AI-assisted wearable for controlling a robot arm remotely and feeling what it touches. Module details: `computer_vision/CLAUDE.md` (camera AI), `bno085_reader/` (IMU armband firmware).

## One-liner (current framing: v3)

PAUL is an AI-assisted wearable teleoperation interface. The operator wears an armband that controls a robotic arm, and the armband also lets them **feel** what the arm touches. The arm does the task, so the person does not have to be there.

## The problem

Some physical tasks have to be done in places where a person should not be present:

- **Quarantine and infectious-disease care:** handing medication, food, or supplies to an isolated patient. This avoids exposure and saves PPE.
- **Radiation-affected areas:** handling samples or debris. PAUL is a lighter-weight complement to gloveboxes and hot cells.
- **Hazardous air:** chemical spills, contaminated labs, and smoke- or gas-filled spaces.

Remote arms already exist. What they usually lack is a sense of touch. The camera shows the scene, but a remote operator cannot feel contact, grip force, or resistance. As a result, objects get dropped, crushed, or forced. PAUL sends the arm's physical interaction back to the operator's body.

## How it works

| Layer | Hardware / approach | Role |
|---|---|---|
| Input | IMU armband: **BNO085** on an **ESP32** (`bno085_reader/`) | Small wrist movements become commands for the **LeRobot SO-101** arm |
| AI vision | Arm camera plus vision model (`computer_vision/`) | Identifies objects, helps select the target, and guides the approach |
| Sensing | **4 thin-film pressure sensors** on the gripper, plus **STS3215** servo feedback | Contact, relative grip strength, and the arm's actual position and load |
| Haptics | **Coin vibration motors** driven by **DRV2605L** haptic controllers | Directional pulses guide motion, intensity tracks contact level, and distinct alerts mark movement limits or resistance |
| Voice | Text-to-speech | Spoken explanations of robot state |
| Recording | Synchronized logs | Camera, motion, and tactile data for review and later robot learning |

The camera shows the operator the scene. The armband tells them what is happening at the gripper.

## User experience loop

1. **Guide:** move the robot with small wrist movements, with AI visual guidance.
2. **Feel:** recognize contact and changes in grip through tactile cues.
3. **Adjust:** refine the grasp and placement based on that feedback.
4. **Understand:** get spoken and tactile explanations of the robot's state.
5. **Record:** capture actions, camera observations, and contact measurements.

## First demo

The demo is a **remote-handling scenario**, for example passing a delicate item to an isolated patient. The arm moves a delicate, lightweight object between two stations in a structured workspace without dropping it or visibly deforming it. The operator uses the camera feed and the armband feedback, and a live UI shows the sensor readings and robot motion.

We record the full interaction and **compare trials with and without haptic feedback**, measuring drops, deformation, and completion time. This tests whether feeling contact helps remote operators handle objects carefully.

## Secondary audiences (earlier framings, still valid)

- **v2: robotics researchers, teleoperators, and students.** Use PAUL to demonstrate tasks while collecting synchronized camera, motion, and tactile data for robot learning, and to teach how commands turn into contact.
- **v1: accessibility.** Blind and low-vision users control the arm through movement and touch, without watching a screen.

## Messaging guidance

- Lead with the v3 story: keep people away from danger without losing the sense of touch.
- Be honest about scope. PAUL is a hackathon prototype with a lightweight hobby arm. It is not a certified medical, nuclear, or hazmat device. Present those settings as the motivating applications, not deployments.
- Say "relative grip strength", not calibrated force, unless the sensors have been calibrated.
