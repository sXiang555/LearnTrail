import { onBeforeUnmount, readonly, shallowRef } from 'vue'
import { api, session } from './api'

const endpoint = '/ms/medias/studio/uploads'
const terminal = new Set(['FAILED', 'EXPIRED', 'ABORTED'])

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted()
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', stop)
      resolve()
    }, ms)
    function stop() {
      clearTimeout(timer)
      reject(signal.reason)
    }
    signal.addEventListener('abort', stop, { once: true })
  })
}

function hashFile(file, signal, onProgress) {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted()
    const worker = new Worker(new URL('./uploadHash.worker.js', import.meta.url), { type: 'module' })
    const finish = (error, hash) => {
      worker.terminate()
      signal.removeEventListener('abort', stop)
      if (error) reject(error)
      else resolve(hash)
    }
    const stop = () => finish(signal.reason)
    signal.addEventListener('abort', stop, { once: true })
    worker.onmessage = ({ data }) => {
      if (data.type === 'progress') onProgress(data.loaded)
      else if (data.type === 'complete') finish(null, data.hash)
      else finish(new Error(data.message))
    }
    worker.onerror = () => finish(new Error('文件校验失败，请重新选择文件'))
    worker.postMessage({ file })
  })
}

function requestKey() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
}

export function useVideoUpload() {
  const capabilities = shallowRef(null)
  const state = shallowRef(null)
  let controller

  async function loadCapabilities(signal) {
    capabilities.value = await api(endpoint + '/capabilities', { signal })
    return capabilities.value
  }

  function pause() { controller?.abort() }
  onBeforeUnmount(pause)

  async function upload(file) {
    if (controller) throw new Error('已有视频正在上传')
    controller = new AbortController()
    const { signal } = controller
    const show = (phase, loaded = 0) => { state.value = { phase, loaded, total: file.size } }
    show('CHECKING')
    try {
      const limits = capabilities.value ?? await loadCapabilities(signal)
      const maxBytes = limits.enabled ? Number(limits.maxFileBytes) : 100 * 1024 ** 2
      if (!file.size || file.size > maxBytes || file.name.trim().length > 100 || !/\.mp4$/i.test(file.name.trim())) {
        throw new Error('请选择符合大小限制的 MP4，文件名最多 100 个字符')
      }
      if (!limits.enabled) {
        const body = new FormData()
        body.append('file', file)
        show('LEGACY')
        return await api('/ms/medias/studio/upload', { method: 'POST', body, signal })
      }
      show('HASHING')
      const sha256 = await hashFile(file, signal, loaded => show('HASHING', loaded))
      const key = ['lt-upload', session.user?.id ?? '', sha256, file.name.trim()].join(':')
      const saved = localStorage.getItem(key)
      const body = { filename: file.name.trim(), size: file.size, sha256, requestKey: saved || requestKey() }
      const initiate = () => api(endpoint, { method: 'POST', body, signal })
      localStorage.setItem(key, body.requestKey)
      let view = await initiate()
      if (terminal.has(view.phase)) {
        body.requestKey = requestKey()
        localStorage.setItem(key, body.requestKey)
        view = await initiate()
      }

      const deadline = Date.now() + 45 * 60 * 1000
      while (Date.now() < deadline) {
        signal.throwIfAborted()
        if (view.phase === 'READY' && view.asset?.ready) {
          localStorage.removeItem(key)
          return view.asset
        }
        if (terminal.has(view.phase)) {
          const errors = { SOURCE_HASH_MISMATCH: '文件完整性校验失败', PROCESS_TIMEOUT: '视频处理超时',
            INVALID_MEDIA: '无法处理该视频，请检查文件格式', TEMPORARY_DISK_FULL: '视频处理空间不足，请稍后重试' }
          throw new Error(errors[view.error] || '视频处理未完成，请重新选择文件重试')
        }

        const url = endpoint + '/' + view.id
        if (view.phase === 'UPLOADING') {
          await sendParts(file, view, url, signal, loaded => show('UPLOADING', loaded))
          show('COMPLETING')
          try {
            view = await api(url + '/complete', { method: 'POST', signal })
          } catch (error) {
            signal.throwIfAborted()
            view = await api(url, { signal })
            if (view.phase === 'UPLOADING') throw error
          }
          continue
        }
        show(view.phase)
        await wait(2000, signal)
        view = view.phase === 'INITIATING' ? await initiate() : await api(url, { signal })
      }
      throw new Error('视频仍在后台处理，稍后选择同一文件可继续查询')
    } catch (error) {
      if (signal.aborted) throw new Error('已暂停，重新选择同一文件可继续')
      throw error
    } finally {
      controller = null
      state.value = null
    }
  }

  return { capabilities: readonly(capabilities), state: readonly(state), loadCapabilities, upload, pause }
}

async function sendParts(file, view, url, signal, progress) {
  const uploaded = new Set(view.parts.map(part => part.number))
  const partSize = Number(view.partSize)
  let loaded = view.parts.reduce((sum, part) => sum + Number(part.size), 0)
  const pending = Array.from({ length: view.partCount }, (_, index) => index + 1).filter(number => !uploaded.has(number))
  let cursor = 0
  let failure
  progress(loaded)
  async function send() {
    while (!failure && cursor < pending.length) {
      const number = pending[cursor++]
      try {
        const start = (number - 1) * partSize
        const chunk = file.slice(start, start + partSize)
        let etag
        for (let attempt = 0; attempt < 3; attempt++) {
          signal.throwIfAborted()
          const signed = await api(url + '/parts/' + number + '/sign', { method: 'POST', signal })
          if (Number(signed.size) !== chunk.size) throw new Error('分片大小不一致，请重新选择视频')
          const response = await fetch(signed.url, {
            method: 'PUT', body: chunk, signal: AbortSignal.any([signal, AbortSignal.timeout(120000)]),
            credentials: 'omit', referrerPolicy: 'no-referrer'
          }).catch(error => {
            signal.throwIfAborted()
            if (attempt === 2) throw error
            return null
          })
          if (response?.ok) {
            etag = response.headers.get('ETag')
            break
          }
          if (attempt < 2) await wait(300 * (attempt + 1), signal)
        }
        if (!etag) throw new Error('分片回执未收到，请继续上传')
        await api(url + '/parts', { method: 'PUT', body: { number, etag }, signal })
        loaded += chunk.size
        progress(loaded)
      } catch (error) {
        failure ||= error
      }
    }
  }
  const parallelism = Math.max(1, Math.min(Number(view.parallelism) || 1, 6))
  await Promise.all(Array.from({ length: Math.min(parallelism, pending.length) }, send))
  if (failure) throw failure
}
