// RDKit.js is a classic script that defines window.initRDKitModule, not an ES module: load it once and start RDKit.
let started = null;

export default function loadRDKit() {
    started ||= new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "vendor/RDKit_minimal.js";
        script.onload = () => window.initRDKitModule({ locateFile: () => "vendor/RDKit_minimal.wasm" }).then(resolve, reject);
        script.onerror = () => reject(new Error("RDKit.js did not load"));
        document.head.append(script);
    });
    return started;
}
