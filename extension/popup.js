const $ = (id) => document.getElementById(id);
async function refresh() {
  const { active, paused } = await chrome.storage.local.get(["active", "paused"]);
  $("paused").checked = !!paused;
  const st = $("status");
  st.textContent = "";
  if (active && Date.now() - active.at < 6 * 3600e3) {
    const { server } = await chrome.runtime.sendMessage({ type: "server" });
    const a = document.createElement("a");
    a.href = `${server}/search/?id=${active.id}`;
    a.target = "_blank";
    a.textContent = "your current search";
    st.append("Matching Myntra pages against ", a, ".");
  } else {
    st.textContent = "No active search. Open a Myntra link from a StyleTrail result to start.";
  }
}
$("paused").onchange = async (e) => { await chrome.storage.local.set({ paused: e.target.checked }); };
$("clear").onclick = async () => { await chrome.storage.local.remove("active"); refresh(); };
refresh();
