# Myra Language Teacher — Task Tracker

## In Progress

### Kids Teacher Spec Gaps
Source: [tasks/kids-teacher-requirements.md](kids-teacher-requirements.md)

- [ ] `High` Add a real admin-only kids-teacher configuration flow for preferences, restrictions, language settings, session defaults, and precedence-safe policy updates
- [ ] `High` Wire raw child-audio retention end-to-end so `KIDS_REVIEW_AUDIO_ENABLED=true` actually persists review audio artifacts
- [ ] `High` Implement unclear-speech and no-speech fallback behavior so empty/unclear turns trigger clarification or gentle reprompts instead of falling through
- [ ] `Medium` Add a live web kids-teacher path that shares the realtime core instead of only showing status and past sessions
- [ ] `Medium` Wire confidence-based multilingual reply selection into the live runtime, including fallback to the configured default language and support for preference ordering
- [ ] `Medium` Add code-level personal-data screening/redaction for persisted kids-teacher review data instead of relying only on profile instructions

### Face Recognition for Reachy Mini
Design doc: [tasks/face-recognition-design.md](face-recognition-design.md)

- [ ] Use the camera for image recognition and auto-recognize Myra
- [ ] Create `src/face_service.py` — camera capture + identify_person()
- [ ] Create `scripts/enroll_faces.py` — enrollment CLI (enroll / list / remove / verify)
- [ ] Create `tests/test_face_service.py` — unit tests (mocked camera + face_recognition)
- [ ] Modify `src/robot_teacher.py` — add `_identify_and_greet()` + wire into `run_lesson_session()`
- [ ] Update `requirements-robot.txt` — add face_recognition, opencv-python-headless
- [ ] Update `.gitignore` — exclude `faces/encodings.pkl` and `faces/*/`
- [ ] Run full test suite — confirm all tests pass
- [ ] On-Pi verification — enroll, verify, run full session

### Language Lesson Polish

- [ ] Add celebratory jingles
- [ ] Use "let's try again with another word <child name>" when the child gets it wrong
- [ ] For every correct word, ensure there is an encouraging line like "great work <child name>" or similar

---

## Completed

_(nothing yet)_
