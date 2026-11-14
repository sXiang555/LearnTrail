<script setup>
import { useId } from 'vue'
defineProps({ tone: { type: String, default: 'forest' }, hero: Boolean })
const id = useId()
</script>

<template>
  <div class="cover-art" :class="[tone, { 'hero-art': hero }]">
    <svg viewBox="0 0 640 400" preserveAspectRatio="xMidYMid slice" role="img" :aria-label="tone === 'forest' ? '绿色几何架构封面' : 'LearnTrail 专栏艺术封面'">
      <defs>
        <pattern :id="id + 'grid'" width="38" height="38" patternUnits="userSpaceOnUse"><path d="M38 0H0V38" fill="none" stroke="currentColor" opacity=".12"/></pattern>
        <linearGradient :id="id + 'light'" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#eff8b8"/><stop offset="1" stop-color="#a5bd68"/></linearGradient>
        <linearGradient :id="id + 'violet'" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#f5f1ff"/><stop offset="1" stop-color="#9d90b9"/></linearGradient>
      </defs>
      <template v-if="tone === 'forest'">
        <rect width="640" height="400" fill="#153e33"/><rect width="640" height="400" :fill="'url(#'+id+'grid)'"/>
        <g transform="translate(365 205) rotate(-28)">
          <ellipse rx="200" ry="74" fill="none" stroke="#d6ec9a" stroke-width="1" opacity=".4"/>
          <ellipse rx="155" ry="155" fill="none" stroke="#c4dca5" stroke-width=".8" opacity=".3"/>
          <ellipse rx="80" ry="180" fill="none" stroke="#c4dca5" stroke-width="1" opacity=".3"/>
          <g transform="rotate(28)">
            <path d="M-74 45l72 42 100-58-74-43z" fill="#7f9c65"/>
            <path d="M-74-67L-2-25V87l-72-42z" fill="#bbd382"/>
            <path d="M-2-25L98-83V29L-2 87z" fill="#8ca763"/>
            <path d="M-74-67L26-125 98-83-2-25z" :fill="'url(#'+id+'light)'"/>
            <path d="M-22-15l72-42v55L-22 40z" fill="#153e33" opacity=".7"/>
            <circle cx="150" cy="-118" r="12" fill="#e8f5b3"/>
            <circle cx="-170" cy="75" r="5" fill="#d4ef9d"/>
          </g>
        </g>
        <text v-if="!hero" x="30" y="45" fill="#d7e9bf" font-size="12" letter-spacing="3">ENGINEERING / FIELDNOTES</text>
        <text v-if="!hero" x="30" y="350" fill="#f2f5da" font-size="46" font-weight="600" letter-spacing="-2">Connect the dots.</text>
        <text x="600" y="370" fill="#d6e6bd" font-size="11" text-anchor="end" letter-spacing="2">LT—01</text>
      </template>
      <template v-else-if="tone === 'clay'">
        <rect width="640" height="400" fill="#dbb69e"/>
        <path d="M0 330l400-120 240 70v120H0z" fill="#b68770" opacity=".35"/>
        <path d="M210 310V150a110 110 0 0 1 220 0v160h-55V153a55 55 0 0 0-110 0v157z" fill="#f2d4bc"/>
        <path d="M250 310V153a70 70 0 0 1 140 0v157h-40V155a30 30 0 0 0-60 0v155z" fill="#bd8e75"/>
        <path d="M390 310V178a64 64 0 0 1 128 0v132h-37V180a27 27 0 0 0-54 0v130z" fill="#ead0bb"/>
        <ellipse cx="340" cy="324" rx="128" ry="16" fill="#795e51" opacity=".15"/>
        <circle cx="205" cy="276" r="49" fill="#f4e6d8"/><circle cx="206" cy="265" r="37" fill="#fbf2e8" opacity=".5"/>
        <text x="30" y="45" fill="#714f42" font-size="12" letter-spacing="3">DESIGN / EXPRESSION</text>
        <text x="30" y="365" fill="#6b4b40" font-size="38" font-weight="500" letter-spacing="-1">Make room for ideas.</text>
      </template>
      <template v-else-if="tone === 'violet'">
        <rect width="640" height="400" fill="#d8d2e7"/><rect width="640" height="400" :fill="'url(#'+id+'grid)'" color="#695b88"/>
        <g transform="translate(250 92) skewY(-14)">
          <path d="M0 100h45V70h45V40h45V10h45v180H0z" :fill="'url(#'+id+'violet)'"/>
          <path d="M180 10l70 25v180l-70-25z" fill="#847697"/>
          <path d="M0 190l70 25h180l-70-25z" fill="#b4aacb"/>
          <rect x="-45" y="-20" width="130" height="90" rx="6" fill="#f8f5ff" stroke="#bfb4d0"/>
          <path d="M-27 4H56M-27 21h52M-27 38h68" stroke="#b2a4ca" stroke-width="5"/>
          <circle cx="185" cy="-15" r="20" fill="#57416d"/>
        </g>
        <text x="30" y="45" fill="#665575" font-size="12" letter-spacing="3">FRONTEND / IN PRACTICE</text>
        <text x="30" y="365" fill="#4c3d61" font-size="46" font-weight="500" letter-spacing="-2">A work in progress.</text>
      </template>
      <template v-else>
        <rect width="640" height="400" fill="#dce5bf"/>
        <circle cx="420" cy="195" r="148" fill="#c8d69a"/><circle cx="420" cy="195" r="111" fill="none" stroke="#7b8e50" stroke-width="1"/>
        <path d="M130 75h255v260H130z" fill="#ebf0d4" transform="rotate(-9 260 200)"/>
        <text x="220" y="265" fill="#4b6032" font-size="180" font-family="Georgia,serif" letter-spacing="-12">{T}</text>
        <text x="30" y="45" fill="#586b38" font-size="12" letter-spacing="3">CODE / OPEN NOTES</text>
        <text x="30" y="365" fill="#4b6032" font-size="42" letter-spacing="-1">Think in possibilities.</text>
      </template>
    </svg>
  </div>
</template>
