import { onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { api, notify, session } from '../api'

export function useLearningReward({ kind, target, enabled, position }) {
  const progress = ref(null)
  const error = shallowRef('')
  let activityId = null
  let busy = false
  let disposed = false
  let generation = 0
  function reset() { generation++; activityId = null; progress.value = null }
  async function tick() {
    if (disposed || !session.token || !target()) return
    if (document.hidden || !enabled()) { if (activityId) reset(); return }
    if (busy || progress.value?.rewarded || progress.value?.eligible) return
    busy = true
    const current = generation
    try {
      const data = activityId
        ? await api('/ls/studio-activities/' + activityId + '/heartbeat', { method: 'POST', body: { position: position() } })
        : await api('/ls/studio-activities/' + kind + '/' + target() + '/start', { method: 'POST' })
      if (disposed || current !== generation) return
      activityId = data.activityId; progress.value = data; error.value = ''
      if (data.awarded > 0) {
        session.balance = data.balance
        notify((kind === 'READ' ? '有效阅读' : '有效观看') + ' +' + data.awarded + ' 积分')
      }
    } catch (e) { if (!disposed && current === generation) error.value = e.message }
    finally { busy = false }
  }
  const visibility = () => { if (document.hidden) reset(); else tick() }
  document.addEventListener('visibilitychange', visibility)
  const timer = setInterval(tick, 10000)
  watch([target, () => session.user?.id], () => { reset(); tick() }, { immediate: true })
  onBeforeUnmount(() => {
    disposed = true; generation++; clearInterval(timer)
    document.removeEventListener('visibilitychange', visibility)
  })
  return { progress, error, retry: tick, pause: reset }
}
