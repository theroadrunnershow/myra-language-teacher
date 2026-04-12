# Myra Language Teacher — Task Tracker

## In Progress

### Face Recognition for Reachy Mini
Design doc: [tasks/face-recognition-design.md](face-recognition-design.md)

- [ ] Create `src/face_service.py` — camera capture + identify_person()
- [ ] Create `scripts/enroll_faces.py` — enrollment CLI (enroll / list / remove / verify)
- [ ] Create `tests/test_face_service.py` — unit tests (mocked camera + face_recognition)
- [ ] Modify `src/robot_teacher.py` — add `_identify_and_greet()` + wire into `run_lesson_session()`
- [ ] Update `requirements-robot.txt` — add face_recognition, opencv-python-headless
- [ ] Update `.gitignore` — exclude `faces/encodings.pkl` and `faces/*/`
- [ ] Run full test suite — confirm all tests pass
- [ ] On-Pi verification — enroll, verify, run full session

---

## Completed

_(nothing yet)_
