// The page's side of the data worker: call(type, ...args) returns a promise of the answer.

export class DataClient {
  constructor(url, config) {
    this.worker = new Worker(url, { type: "module" });
    this.next = 1;
    this.waiting = new Map();
    this.worker.onmessage = (e) => {
      const { id, result, error } = e.data;
      const w = this.waiting.get(id);
      if (!w) return;
      this.waiting.delete(id);
      error ? w.reject(new Error(error)) : w.resolve(result);
    };
    this.worker.onerror = (e) => {
      for (const w of this.waiting.values()) w.reject(new Error(e.message || "data worker failed"));
      this.waiting.clear();
    };
    this.ready = this.call("init", config);
  }

  call(type, ...args) {
    const id = this.next++;
    return new Promise((resolve, reject) => {
      this.waiting.set(id, { resolve, reject });
      this.worker.postMessage({ id, type, args });
    });
  }
}
