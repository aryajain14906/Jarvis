const button = document.getElementById("askBtn");
button.addEventListener("click", askQuestion);
document
    .getElementById("question")
    .addEventListener("keydown", (e) => {

        if (e.key === "Enter") {
            askQuestion();
        }

    });
const SpeechRecognition =
    window.SpeechRecognition ||
    window.webkitSpeechRecognition;

let recognition = null;

if (SpeechRecognition) {

    recognition = new SpeechRecognition();

    recognition.lang = "en-IN";

    recognition.continuous = true;

    recognition.interimResults = true;

    recognition.onerror = (event) => {
    console.error("SpeechRecognition Error:", event.error);

    isListening = false;
    micBtn.classList.remove("recording");
    status.innerText = "Error: " + event.error;
};

}
// ============================================
// CHAT HISTORY
// ============================================
let chatHistory = [];

function appendMessage(role, text) {
    const container = document.getElementById("chat-history");
    if (!container) return;
    const bubble = document.createElement("div");
    bubble.className = role === "user" ? "user-msg" : "jarvis-msg";
    bubble.innerText = text;
    container.appendChild(bubble);
    container.scrollTop = container.scrollHeight;
}

// ============================================
// MARKDOWN STRIP (so TTS doesn't say "asterisk asterisk")
// ============================================
function cleanForSpeech(text) {
    return text
        .replace(/\*\*/g, "")
        .replace(/\*/g, "")
        .replace(/`/g, "")
        .replace(/#/g, "")
        .trim();
}

// ============================================
// VOICE STATE
// ============================================
let voiceEnabled = true;
let useBackendVoice = false; // false = Web Speech API (instant), true = local pyttsx3 via /speak
let currentAudio = null;     // tracks server-generated audio for stop/mute
let jarvisVoice = null;

// ============================================
// APPROACH 1: WEB SPEECH API (instant, zero backend calls)
// ============================================
function initJarvisVoice() {
    const voices = window.speechSynthesis.getVoices();
    if (!voices.length) return;

    const preferredNames = [
        "Google UK English Male",
        "Microsoft Ryan Online (Natural) - English (United Kingdom)",
        "Microsoft George - English (United Kingdom)",
        "Daniel",
        "Arthur"
    ];

    for (const name of preferredNames) {
        const match = voices.find(v => v.name === name);
        if (match) { jarvisVoice = match; return; }
    }

    jarvisVoice = voices.find(v => v.lang === "en-GB" && /male|daniel|george|ryan/i.test(v.name))
               || voices.find(v => v.lang === "en-GB")
               || voices[0];
}
window.speechSynthesis.onvoiceschanged = initJarvisVoice;
initJarvisVoice();

function speakAsJarvis(text) {
    if (!voiceEnabled || !text?.trim()) return;

    window.speechSynthesis.cancel(); // clear any pending queue first

    const utterance = new SpeechSynthesisUtterance(text);
    if (jarvisVoice) utterance.voice = jarvisVoice;

    utterance.pitch = 0.85;
    utterance.rate = 0.95;
    utterance.volume = 1.0;
    utterance.lang = "en-GB";
    utterance.onerror = (e) => console.warn("Speech synthesis error:", e.error);

    window.speechSynthesis.speak(utterance);
}

// ============================================
// APPROACH 2: LOCAL BACKEND VOICE via /speak (pyttsx3, fully offline)
// ============================================
async function speakAsJarvisBackend(text) {
    if (!voiceEnabled || !text?.trim()) return;

    if (currentAudio) {
        currentAudio.pause();
        currentAudio.currentTime = 0;
    }

    try {
        const response = await fetch("http://127.0.0.1:8000/speak", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text })
        });

        const data = await response.json();
        if (data.error) throw new Error(data.error);

        const audioSrc = `data:audio/${data.audio_format};base64,${data.audio_base64}`;
        currentAudio = new Audio(audioSrc);
        currentAudio.volume = 1.0;

        currentAudio.play().catch(err => {
            // Autoplay blocked until first user interaction — expected on first load
            console.warn("Autoplay blocked, will resume after user interaction:", err);
        });
    } catch (err) {
        console.error("Backend TTS request failed, falling back to Web Speech:", err);
        speakAsJarvis(text); // graceful fallback if backend/pyttsx3 fails
    }
}

// ============================================
// UNIFIED SPEAK DISPATCHER
// ============================================
function speak(text) {
    if (useBackendVoice) {
        speakAsJarvisBackend(text);
    } else {
        speakAsJarvis(text);
    }
}

// ============================================
// AUTOPLAY UNLOCK (browsers block audio before user gesture)
// ============================================
let audioUnlocked = false;
function unlockAudio() {
    if (audioUnlocked) return;
    const primer = new SpeechSynthesisUtterance(" ");
    primer.volume = 0;
    window.speechSynthesis.speak(primer);
    audioUnlocked = true;
}
document.addEventListener("click", unlockAudio, { once: true });
document.addEventListener("keydown", unlockAudio, { once: true });
const micBtn = document.getElementById("micBtn");
const status = document.getElementById("listening-status");

let isListening = false;

if (recognition) {

    micBtn.addEventListener("click", () => {

        if (!isListening) {

            recognition.start();

        } else {

            recognition.stop();

        }

    });

    recognition.onstart = () => {

    isListening = true;

    micBtn.classList.add("recording");

    status.innerText = "Listening...";

    document.getElementById("question").disabled = true;

    document.getElementById("answer").innerText = "🎤 Listening...";

};

    recognition.onend = () => {
    console.log("Recognition ended");

    isListening = false;

    micBtn.classList.remove("recording");

    status.innerText = "";

    document.getElementById("question").disabled = false;

};



    recognition.onresult = (event) => {

    let transcript = "";

    for (let i = event.resultIndex; i < event.results.length; i++) {

        const result = event.results[i];

        console.log("---------");

        for (let j = 0; j < result.length; j++) {

            console.log(
                "Alternative:",
                result[j].transcript,
                "Confidence:",
                result[j].confidence
            );

        }

        transcript += result[0].transcript;

    }

    document.getElementById("question").value = transcript;

    if (
        event.results[event.results.length - 1].isFinal &&
        transcript.trim() !== ""
    ) {
        askQuestion();
    }

};

} else {

    micBtn.disabled = true;

    micBtn.innerText = "❌";

}
// ============================================
// TOGGLE CONTROLS
// ============================================
function toggleJarvisVoice() {
    voiceEnabled = !voiceEnabled;
    if (!voiceEnabled) {
        window.speechSynthesis.cancel();
        if (currentAudio) currentAudio.pause();
    }
    const btn = document.getElementById("voice-toggle-btn");
    btn.textContent = voiceEnabled ? "🔊 Voice: ON" : "🔇 Voice: OFF";
    btn.setAttribute("aria-pressed", String(voiceEnabled));
}

function toggleVoiceQuality() {
    useBackendVoice = !useBackendVoice;
    const btn = document.getElementById("voice-quality-btn");
    btn.textContent = useBackendVoice ? "🖥️ Backend Voice (pyttsx3)" : "⚡ Instant Voice (browser)";
}

// ============================================
// YOUR EXISTING askQuestion() — MODIFIED to log history + speak clean text
// ============================================
async function askQuestion() {
    const questionInput = document.getElementById("question");
    const question = questionInput.value.trim();
    if (!question) return;

    const answerDiv = document.getElementById("answer");

    chatHistory.push({ role: "user", content: question });
    appendMessage("user", question);
    questionInput.value = "";
    answerDiv.innerText = "Thinking...";

    try {
        const response = await fetch("http://127.0.0.1:8000/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: question })
        });

        const data = await response.json();

        chatHistory.push({ role: "assistant", content: data.answer });
        appendMessage("assistant", data.answer);
        answerDiv.innerText = "";

        speak(cleanForSpeech(data.answer)); // speaks the cleaned response

    } catch (error) {
        answerDiv.innerText = "Error connecting to backend.";
        console.error(error);
    }
}