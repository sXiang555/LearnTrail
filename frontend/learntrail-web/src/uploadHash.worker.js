import { createSHA256 } from 'hash-wasm'

self.onmessage = async ({ data: { file } }) => {
  try {
    const hash = await createSHA256()
    hash.init()
    const chunkSize = 4 * 1024 * 1024
    for (let offset = 0; offset < file.size; offset += chunkSize) {
      const buffer = await file.slice(offset, offset + chunkSize).arrayBuffer()
      hash.update(new Uint8Array(buffer))
      self.postMessage({ type: 'progress', loaded: Math.min(offset + chunkSize, file.size), total: file.size })
    }
    self.postMessage({ type: 'complete', hash: hash.digest('hex') })
  } catch {
    self.postMessage({ type: 'error', message: '文件校验失败，请重新选择文件' })
  }
}
