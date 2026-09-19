# Arrow-air/project-quiver #256 — T-09 M0 application packet

**ECON:** VERIFIED >=$50. The upstream issue advertises **$500 total** after the unpaid M0 gate: **M1 $300 + M2 $200**.

**Upstream:** https://github.com/Arrow-air/project-quiver/issues/256  
**Upstream state checked 2026-09-19:** OPEN, claimable, no assignee, no development PR shown.  
**Purpose of this file:** preserve the exact M0 text on an owner-controlled durable surface so a GitHub-authenticated seat can post it to #256 without redoing research. This file is not an upstream submission, assignment, acceptance, or payment receipt.

## Paste-ready M0 comment

I'd like to apply for T-09. I followed the issue's M0 rule: source index first, then ten decision-register rows drawn from at least three phases. I kept open/unstated rationale open instead of filling gaps with inference.

### Source index used for this sample

1. **PT1 Engineering Report**  
   https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT1-Engineering-Report.md  
   Initial architecture and requirements: common quad layout, 25 kg class, 14S battery concept, CAN-oriented communications/monitoring, payload interface, propulsion assumptions.

2. **PT2 Engineering Report**  
   https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT2-Engineering-Report.md  
   PT2 architecture and requirements: Mateksys H743-SLIM V3 baseline, integrated PCB/SSR power-and-signal management, wiring/protection rules, retained attachment concept.

3. **PT3 Engineering Report**  
   https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT3-Engineering-Report.md  
   PT3 changes: distributed four-PCB architecture, Pix32 V6 selection, three attachment interfaces, redundant GNSS, combined radar/LiDAR altitude sensing, Raspberry Pi role.

4. **Dev-Kit Engineering Report**  
   https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/Dev-Kit-Engineering-Report.md  
   Dev-Kit field/evolution record: structural weight decision after loaded flight testing, Remote ID, Hub/SDK evolution, obstacle-avoidance validation, current weight/payload configuration.

5. **Dev Kit Main PCB Update**  
   https://github.com/Arrow-air/project-quiver/blob/main/task-grant-bounty/Dev-Kit/Main-PCB-Update.md  
   Manufacturing/test-driven PCB changes: JST-GH connector layout, redundant SSR control, backup 5 V, protection, payload/HV MOSFET control, dedicated routing/layer strategy.

6. **Quiver Structure Weight Reduction**  
   https://github.com/Arrow-air/project-quiver/blob/main/task-grant-bounty/Dev-Kit/Structure-Weight-Reduction.md  
   FEA + physical + flight evidence for which thickness reductions were retained and which were reversed.

7. **Quiver SDK Information Note**  
   https://github.com/Arrow-air/project-quiver/blob/main/task-grant-bounty/Dev-Kit/Quiver-SDK.md  
   Companion/payload integration strategy, open-standards interfaces, Raspberry-Pi services, payload pipeline and developer-enablement direction.

8. **Obstacle Avoidance System Overview**  
   https://github.com/Arrow-air/project-quiver/blob/main/task-grant-bounty/Dev-Kit/Obstacle-Avoidance.md  
   Current sensor mix, custom ArduPilot S2L support, BendyRuler selection and validated low-speed/clutter parameter rationale.

### Ten sample decision-register rows

| ID | Phase | Decision | Replaced | Why | Evidence | Status |
| --- | --- | --- | --- | --- | --- | --- |
| D-001 | PT1 | Use a conventional quadcopter layout. | More complex multirotor layouts were not selected for the initial prototype. | PT1 explicitly ties the common quad layout to **energy efficiency and structural simplification**. | [PT1 report — Introduction](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT1-Engineering-Report.md) | Standing through later phases |
| D-002 | PT1 | Build around a 14-cell LiHV smart-battery concept and use CAN/digital component communication where practical. | More reliance on traditional non-differential PWM/analog-style signaling. | PT1 says CAN is used to reduce susceptibility to electromagnetic interference and digital communications expose component health such as battery-cell and ESC temperature. | [PT1 report — Introduction / electrical requirements](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT1-Engineering-Report.md) | Evolved, not abandoned |
| D-003 | PT2 | Use a Mateksys H743-SLIM V3 as the PT2 flight controller. | PT1 prototype specification named a Pixhawk 6X. | **The PT2 report records the change but does not state a source-backed reason in the section I read. I am leaving the rationale open rather than inventing one.** | [PT1 report §3.1](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT1-Engineering-Report.md), [PT2 report §3.1](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT2-Engineering-Report.md) | Superseded in PT3 |
| D-004 | PT2 | Integrate power and signal management on a PCB with solid-state relays, with short labeled wiring plus dedicated relays/fuses. | PT1's testbed PDB/contactor-oriented power-distribution approach. | PT2 explicitly says the PCB/SSR approach improves **safety and controllability**; the electrical requirements call for short labeled wiring for maintenance/fault isolation and dedicated protection against shorts/surges. | [PT1 report — Introduction](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT1-Engineering-Report.md), [PT2 report — Introduction / §3.4](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT2-Engineering-Report.md) | Superseded by PT3 distributed PCB architecture |
| D-005 | PT3 | Split electronics into four custom boards: Battery PCB, Main PCB, FC PCB and Attachment Interface PCB. | PT2's single centralized PCB approach. | PT3 states the distributed layout reduces interdependencies, lets each board be optimized for its role, and simplifies maintenance/troubleshooting/upgrades. | [PT3 report — Electronics Integration](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT3-Engineering-Report.md) | Standing basis for Dev-Kit |
| D-006 | PT3 | Select Pix32 V6 as the baseline flight controller after comparing Pixhawk 6X/Pro, CubePilot+, uAvionix George, Auterion Skynode and H743-SLIM V3. | PT2 Mateksys H743-SLIM V3 baseline. | PT3 says Pix32 V6 balances **performance and affordability for prototyping**, integrates with the Main PCB, and leaves a clear later upgrade path to Pixhawk 6X or Cube Orange. | [PT3 report — Flight Controller Selection & Integration](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT3-Engineering-Report.md) | Standing PT3/Dev-Kit baseline |
| D-007 | PT3 | Use redundant navigation: an F9P-class primary RTK GNSS plus a Mateksys M9N-G4-3100 backup, with the primary raised on a 2 cm arch. | Earlier single-GNSS prototype arrangements. | PT3 explicitly cites redundancy/failover, while the raised primary mount reduces electromagnetic interference and improves satellite line of sight. | [PT3 report — Navigation and Altimetry Systems](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT3-Engineering-Report.md) | Standing |
| D-008 | PT3 | Accommodate both Ainstein US-D1 radar and Benewake TF03-180 LiDAR for altitude sensing. | PT1 radar-only emphasis and PT2 LiDAR-only reliance. | PT3 says the two sensing modes cover different conditions/terrain: radar remains robust in fog/dust/rain while LiDAR gives high-resolution low-altitude measurements; the autopilot fuses them. | [PT3 report — Navigation and Altimetry Systems](https://github.com/Arrow-air/project-quiver/blob/main/docs/Engineering-Reports/PT3-Engineering-Report.md) | Standing |
| D-009 | Dev Kit | Keep thickness reductions on the upper plates/beams but restore the lower plate and battery walls to original thickness. | The initial test-frame plan that halved thickness on both upper and lower structures. | FEA and physical inspection showed lower-structure deformation; a no-payload flight had increased lateral vibration, and a ~7 kg payload flight produced oscillation severe enough to abort. The final note keeps only the upper reductions to retain support for heavy payloads. | [Structure Weight Reduction — §§1–3](https://github.com/Arrow-air/project-quiver/blob/main/task-grant-bounty/Dev-Kit/Structure-Weight-Reduction.md) | Standing Dev-Kit decision |
| D-010 | Dev Kit | Revise the Main PCB around more robust power/control and serviceability: JST-GH connectors, redundant SSR control, backup 5 V, MOSFET-driven payload/HV switching, added protection and clearer power/CAN/Ethernet layout strategy. | PT3 Main PCB implementation before build/test feedback. | The note says the changes were collected during PT3 manufacturing, assembly and testing; the redesign targets efficient wiring, more robust power delivery, protection and clearer/serviceable integration. | [Dev Kit Main PCB Update](https://github.com/Arrow-air/project-quiver/blob/main/task-grant-bounty/Dev-Kit/Main-PCB-Update.md) | Standing Dev-Kit revision |

If this sample passes the citation gate, I'm ready to build the complete 60–100 row register as M1. I will keep disagreements explicit, preserve open questions as open, and use the issue/PR/log that actually records each decision rather than backfilling generic rationale.

---

## Validation notes for the swarm

- #256 itself says M0 is **unpaid** and assignment follows an accepted M0; paid work must not start before that acceptance.
- The same issue remains the authoritative submission surface. A prior direct GitHub connector attempt returned `403 Resource not accessible by integration`; no comment materialized.
- Do not email or invent an alternate claim route: #256 documents issue-comment M0 as the gate.
- An authenticated GitHub browser/user surface should post the block above **once**, then return the canonical comment URL and reviewer response.
