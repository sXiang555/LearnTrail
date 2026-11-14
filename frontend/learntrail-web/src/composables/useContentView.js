import { onBeforeUnmount, shallowRef, watch } from 'vue'
import { api, session } from '../api.js'

// A lightweight engagement metric, independent of learning rewards and progress.
export function useContentView({ kind, target, enabled, position, onCount }) {
  const error = shallowRef('')
  let generation = 0, seconds = 0, lastTime = null, lastPosition = null
  let done = false, busy = false, disposed = false
  function reset() {
    generation++; seconds = 0; lastTime = null; lastPosition = null
    done = false; busy = false; error.value = ''
  }
  async function tick() {
    if (disposed || done || busy || error.value) return
    if (!session.token || !target() || document.hidden || !enabled()) {
      lastTime = null; lastPosition = null; return
    }
    const now = performance.now()
    const currentPosition = position?.()
    if (lastTime !== null) {
      const elapsed = (now - lastTime) / 1000
      let credit = elapsed > 0 && elapsed <= 2.5 ? elapsed : 0
      if (position) {
        const moved = currentPosition - lastPosition
        if (!Number.isFinite(moved) || moved <= 0 || moved > elapsed * 2.5 + 1) credit = 0
        else credit = Math.min(credit, moved)
      }
      seconds += credit
    }
    lastTime = now; lastPosition = currentPosition
    if (seconds < 5) return
    const current = generation
    busy = true
    try {
      const result = await api('/rs/likes/content/' + kind + '/' + target() + '/view', { method: 'POST' })
      if (!disposed && current === generation) { done = true; onCount?.(result.viewTimes) }
    } catch (e) {
      if (!disposed && current === generation) error.value = e.message
    } finally { if (current === generation) busy = false }
  }
  const timer = setInterval(tick, 1000)
  const visibility = () => { lastTime = null; lastPosition = null }
  document.addEventListener('visibilitychange', visibility)
  watch([target, () => session.user?.id, () => Boolean(session.token)], reset, { immediate: true })
  onBeforeUnmount(() => {
    disposed = true; generation++; clearInterval(timer)
    document.removeEventListener('visibilitychange', visibility)
  })
  return { error, retry: () => { error.value = ''; return tick() } }
}
