<script setup>
import { computed, onMounted, ref } from "vue";
const repository = "https://github.com/firstprayer/agent-home-public";
const release = `${repository}/releases/tag/v0.3.2`;
const download = `${repository}/releases/download/v0.3.2/Agent-Home-0.3.2-macOS-arm64.dmg`;
const mark = `${import.meta.env.BASE_URL}mark.svg`;
const selected = ref("text");
const examples = {
  text: {
    label: "Text",
    time: "09:41",
    ask: "Pick up where we left off. What’s next?",
    answer:
      "The release build is ready. I’m checking the last two changes before we ship.",
  },
  image: {
    label: "Photo",
    time: "09:42",
    ask: "How does this look on mobile?",
    answer:
      "I can see the layout. The navigation needs more room on smaller screens. I’ll adjust it.",
  },
  voice: {
    label: "Voice",
    time: "09:43",
    ask: "A thought on the way home.",
    answer:
      "You asked: Can we simplify the first-run setup? I’ll start with the steps a new user actually needs.",
  },
  video: {
    label: "Video",
    time: "09:44",
    ask: "Can you look at this interaction?",
    answer:
      "I’ve inspected the clip with the available local tools. The panel opens twice. I’ll trace the event handler.",
  },
};
const example = computed(() => examples[selected.value]);
onMounted(() => {
  document.getElementById(location.hash.slice(1))?.scrollIntoView();
});
</script>

<template>
  <a class="skip" href="#main">Skip to content</a>
  <header class="site-header wrap">
    <a class="brand" href="#main"
      ><img :src="mark" alt="" width="38" height="38" />Agent Home</a
    >
    <nav aria-label="Main navigation">
      <a href="#how-it-works">How it works</a
      ><a href="#features">Inside the app</a><a href="#questions">Questions</a
      ><a :href="repository" class="nav-source"
        >GitHub <span aria-hidden="true">↗</span></a
      >
    </nav>
  </header>
  <main id="main">
    <section class="hero wrap" aria-labelledby="hero-title">
      <div class="hero-copy">
        <p class="eyebrow">
          <span class="dot"></span> A LITTLE CLOSER TO YOUR CODEX.
        </p>
        <h1 id="hero-title">
          Talk to your<br />Codex agent,<br /><em>anywhere.</em>
        </h1>
        <p class="intro">
          Your computer does the work.<br />Your conversation goes with you.
        </p>
        <p class="hero-description">
          Connect Telegram to Codex on your machine. Pick up the same session
          from your phone — with messages, photos, videos, and voice notes.
        </p>
        <div class="hero-actions">
          <a class="button" href="#start"
            >Get Agent Home <span aria-hidden="true">↗</span></a
          ><a class="text-link" href="#how-it-works"
            >Take a look <span aria-hidden="true">↓</span></a
          >
        </div>
        <p class="hero-note">
          Free &amp; open source <span>·</span> macOS first <span>·</span> Your
          Codex account
        </p>
      </div>
      <figure class="demo-stage" aria-labelledby="demo-caption">
        <div class="desktop-card">
          <div class="window-bar">
            <span class="traffic-lights" aria-hidden="true"
              ><i></i><i></i><i></i></span
            ><span>Agent Home</span><span class="local-label">LOCAL</span>
          </div>
          <div class="desktop-heading">
            <img :src="mark" alt="" width="26" height="26" />
            <div>
              <strong>Your agents.</strong><small>A place to call home.</small>
            </div>
            <span class="desktop-count">03</span>
          </div>
          <div class="desktop-table">
            <div class="table-labels">
              <span>IDENTITY</span><span>RUNTIME</span><span>STATUS</span>
            </div>
            <div
              v-for="agent in [
                { name: 'Research', initials: 'RE', status: 'Idle' },
                { name: 'Studio', initials: 'ST', status: 'Working' },
                { name: 'Operations', initials: 'OP', status: 'Ready' },
              ]"
              :key="agent.name"
              class="desktop-row"
            >
              <span class="agent-name"
                ><span class="avatar" :class="agent.initials">{{
                  agent.initials
                }}</span
                >{{ agent.name }}</span
              ><span class="runtime-label">{{
                agent.name === "Operations" ? "Admin" : "Codex"
              }}</span
              ><span class="status" :class="agent.status.toLowerCase()">{{
                agent.status
              }}</span>
            </div>
          </div>
          <div class="desktop-foot">
            <span class="dot"></span> Identity stays. Sleeves can change.
          </div>
        </div>
        <div class="connection-trace" aria-hidden="true">
          <span></span><i>↗</i>
        </div>
        <div class="phone">
          <div class="phone-status">
            <strong>{{ example.time }}</strong
            ><span aria-hidden="true">▮▮▮ ▰</span>
          </div>
          <div class="phone-header">
            <span class="back-arrow" aria-hidden="true">‹</span
            ><span class="phone-avatar">ST</span>
            <div><strong>Studio</strong><small>your Codex agent</small></div>
            <span class="phone-menu" aria-hidden="true">···</span>
          </div>
          <div class="chat">
            <span class="chat-day">ILLUSTRATIVE CONVERSATION</span>
            <div class="message outgoing">
              <div
                v-if="selected === 'image'"
                class="image-preview"
                aria-label="Example layout image"
              >
                <span class="preview-nav"></span
                ><strong>A fresh<br />perspective.</strong
                ><span class="preview-card"></span>
              </div>
              <div v-if="selected === 'voice'" class="voice-preview">
                <span class="play" aria-hidden="true">▶</span
                ><span class="waveform" aria-hidden="true"
                  ><i
                    v-for="(height, index) in [
                      9, 16, 25, 12, 31, 20, 35, 17, 27, 10, 23, 32, 14, 28, 19,
                      11, 25, 15, 29, 9,
                    ]"
                    :key="index"
                    :style="{ '--bar-height': `${height}px` }"
                  ></i></span
                ><small>0:08</small>
              </div>
              <div v-if="selected === 'video'" class="video-preview">
                <span aria-hidden="true">▶</span
                ><small>Interaction.mp4 · 0:12</small>
              </div>
              <p>{{ example.ask }}</p>
              <time>{{ example.time }} <span aria-hidden="true">✓✓</span></time>
            </div>
            <div class="message incoming">
              <p>{{ example.answer }}</p>
              <time>{{ example.time }}</time>
            </div>
          </div>
          <div class="phone-composer">
            <span aria-hidden="true">＋</span><span>Your next idea…</span
            ><i aria-hidden="true">↑</i>
          </div>
          <div class="phone-home" aria-hidden="true"></div>
        </div>
        <figcaption id="demo-caption">
          Example workspace · Try a different way to talk.
        </figcaption>
        <div class="demo-tabs" aria-label="Conversation examples">
          <button
            v-for="(item, key) in examples"
            :key="key"
            :aria-pressed="selected === key"
            @click="selected = key"
          >
            {{ item.label }}
          </button>
        </div>
      </figure>
    </section>
    <div class="promise-strip wrap">
      <span>Same Codex session.</span><span>Your own computer.</span
      ><span>A chat you already know.</span>
    </div>
    <section id="how-it-works" class="how wrap" aria-labelledby="how-title">
      <div class="section-heading">
        <p class="eyebrow">MAKE YOURSELF AT HOME.</p>
        <h2 id="how-title">A few connections.<br />A lot more freedom.</h2>
        <p>
          Set it up on the computer where you use Codex. Agent Home brings the
          conversation to Telegram.
        </p>
      </div>
      <ol class="steps">
        <li>
          <span class="step-index">01</span>
          <h3>Open your home base.</h3>
          <p>
            A native macOS window. Your agents, sessions, and connection status
            in one calm overview.
          </p>
        </li>
        <li>
          <span class="step-index">02</span>
          <h3>Choose a Codex session.</h3>
          <p>
            Use a loaded root session, or create one in an allowed project
            folder. Each identity has one current sleeve.
          </p>
        </li>
        <li>
          <span class="step-index">03</span>
          <h3>Say hello in Telegram.</h3>
          <p>
            Connect a bot, explicitly allow your Telegram account, and send a
            message. Replies come back to the same chat.
          </p>
        </li>
      </ol>
    </section>
    <section id="features" class="features" aria-labelledby="features-title">
      <div class="wrap">
        <div class="feature-heading">
          <div>
            <p class="eyebrow">MADE FOR THE WAY IDEAS HAPPEN.</p>
            <h2 id="features-title">
              Away from the desk.<br />Still in the conversation.
            </h2>
          </div>
          <p>
            The thought on the train. The screenshot on your phone. The next
            step on a walk.
          </p>
        </div>
        <div class="feature-grid">
          <article>
            <span class="feature-symbol" aria-hidden="true">↳</span>
            <h3>Pick up the same thread.</h3>
            <p>
              Messages reach the exact Codex session you assigned. Continue the
              work already there, or steer a turn that’s running.
            </p>
          </article>
          <article>
            <span class="feature-symbol" aria-hidden="true">◫</span>
            <h3>Bring more than words.</h3>
            <p>
              Photos become image inputs. Videos and voice notes become local
              files your agent can inspect with its available tools.
            </p>
          </article>
          <article>
            <span class="feature-symbol" aria-hidden="true">⌂</span>
            <h3>A home for each project.</h3>
            <p>
              Keep separate identities for separate work. Change the runtime
              sleeve while keeping the identity and Telegram connection.
            </p>
          </article>
          <article>
            <span class="feature-symbol" aria-hidden="true">◎</span>
            <h3>Know who’s connected.</h3>
            <p>
              See runtime status, bot listeners, queued replies, and recent
              activity. Explicit sender policies keep access in your hands.
            </p>
          </article>
        </div>
        <aside class="local-note">
          <span aria-hidden="true">↗</span>
          <div>
            <strong>Your machine is home base.</strong>
            <p>
              Agent Home stores its database and received files locally.
              Telegram and your configured Codex provider handle their
              respective communications. Keep the host computer awake and
              online.
            </p>
          </div>
        </aside>
      </div>
    </section>
    <section
      id="questions"
      class="questions wrap"
      aria-labelledby="questions-title"
    >
      <div class="section-heading">
        <p class="eyebrow">A FEW GOOD QUESTIONS.</p>
        <h2 id="questions-title">Before you head out.</h2>
        <p>A small app. Clear boundaries.</p>
      </div>
      <div class="faq">
        <details>
          <summary>Is Agent Home free?</summary>
          <p>
            Yes. Agent Home is free and open source. You bring your own Codex
            account and Telegram bot. Any usage charges from your configured
            model service remain separate.
          </p>
        </details>
        <details>
          <summary>Does my computer need to stay on?</summary>
          <p>
            Yes. Agent Home and Codex run on your computer. Keep it awake,
            connected to the internet, and running the app. The built-in Admin
            sleeve also depends on this app being online.
          </p>
        </details>
        <details>
          <summary>Can I use my existing Codex conversation?</summary>
          <p>
            Yes. Choose a loaded root session in the app. Agent Home routes by
            its exact session address. Changing to a different session does not
            automatically copy the previous conversation’s context.
          </p>
        </details>
        <details>
          <summary>What happens if I restart the app?</summary>
          <p>
            Your identities, access policies, and assigned session addresses
            stay in SQLite. Agent Home reconnects to existing sessions when
            Codex is available. Restoring a stopped managed CLI is an explicit
            action in the app.
          </p>
        </details>
        <details>
          <summary>What can I send?</summary>
          <p>
            Text, photos, videos, image/video documents, and Telegram voice
            notes. Incoming files are limited to 20 MB by Telegram’s cloud Bot
            API. Voice and video decoding depend on the tools available to your
            agent.
          </p>
        </details>
        <details>
          <summary>Which platforms are supported?</summary>
          <p>
            The native desktop app targets macOS first. The Python server can
            run on macOS and Linux. Windows support is planned; it is not
            currently supported.
          </p>
        </details>
      </div>
    </section>
    <section id="start" class="start wrap" aria-labelledby="start-title">
      <div class="start-copy">
        <p class="eyebrow">YOUR NEXT IDEA DOESN’T NEED A DESK.</p>
        <h2 id="start-title">Make yourself<br /><em>at home.</em></h2>
        <p>
          Install and sign in to Codex CLI first. Then give your agent a way to
          reach you.
        </p>
        <a class="button download-link" :href="download"
          >Download for macOS <span aria-hidden="true">↓</span></a
        >
        <p class="release-note">
          Free under AGPLv3. Apple Silicon · macOS 15 or later.<br />
          <a :href="release">v0.3.2 preview · Release notes &amp; checksums ↗</a>
        </p>
      </div>
      <div class="install">
        <div class="install-top">
          <h3>macOS preview</h3>
          <span class="local-label">v0.3.2</span>
        </div>
        <p>
          Download the DMG, drag <strong>Agent Home</strong> into Applications,
          then open it. Your dashboard appears inside the app.
        </p>
        <p>
          Install and sign in to Codex CLI separately. The in-app
          <strong>Get started</strong> guide walks you through connecting your
          first agent and Telegram bot. Python and Node are not required for
          the download.
        </p>
        <p class="build-note">
          This Apple Silicon preview is ad-hoc signed and not Apple notarized.
          If macOS blocks it, review the source and follow
          <a href="https://support.apple.com/en-us/102445">Apple’s Open Anyway instructions</a>.
        </p>
        <details>
          <summary>Build from source, including Intel Macs</summary>
          <p>Requires uv and Xcode Command Line Tools.</p>
          <pre aria-label="Build the macOS app"><code>git clone https://github.com/firstprayer/agent-home-public.git
cd agent-home-public
uv run --managed-python --group macos-build python packaging/macos/build.py</code></pre>
          <p>The DMG is created in <code>dist/</code> for your Mac’s architecture.</p>
        </details>
        <details>
          <summary>Run the server on macOS or Linux</summary>
          <pre><code>uv sync --locked
uv run python bridge.py</code></pre>
          <p>Open <code>http://127.0.0.1:8765/</code> on the host computer.</p>
        </details>
        <a class="text-link" :href="`${repository}#readme`"
          >Read the full guide <span aria-hidden="true">↗</span></a
        >
      </div>
    </section>
  </main>
  <footer class="wrap">
    <a class="brand" href="#main"
      ><img :src="mark" alt="" width="32" height="32" />Agent Home</a
    >
    <p>Talk to your Codex agent, anywhere.</p>
    <a :href="repository">By firstprayer <span aria-hidden="true">↗</span></a>
  </footer>
</template>
