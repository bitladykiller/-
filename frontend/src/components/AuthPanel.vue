<script setup lang="ts">
import { ref } from "vue";
import { useChatStore } from "@/stores/chat";

const store = useChatStore();

const mode = ref<"login" | "register">("login");
const username = ref("");
const password = ref("");
const submitting = ref(false);
const message = ref<string | null>(null);

async function onSubmit() {
  if (submitting.value) return;
  message.value = null;
  const name = username.value.trim();
  if (!name || !password.value) {
    message.value = "请输入用户名和密码";
    return;
  }
  submitting.value = true;
  try {
    if (mode.value === "login") {
      await store.login(name, password.value);
    } else {
      await store.registerAccount(name, password.value);
    }
  } catch (e) {
    message.value = e instanceof Error ? e.message : String(e);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="auth-wrap">
    <div class="auth-card plaque">
      <div class="mark" aria-hidden="true"><span>AG</span></div>
      <h1>AssistGen</h1>
      <p class="sub">智能客服 · 礼宾控制台</p>
      <p class="hint">演示账号 demo_user ✦ 密码 demo1234</p>

      <div class="tabs">
        <button
          type="button"
          :class="{ active: mode === 'login' }"
          @click="mode = 'login'"
        >
          登录
        </button>
        <button
          type="button"
          :class="{ active: mode === 'register' }"
          @click="mode = 'register'"
        >
          注册
        </button>
      </div>

      <form @submit.prevent="onSubmit">
        <label>
          用户名
          <input
            v-model="username"
            autocomplete="username"
            placeholder="demo_user"
          />
        </label>
        <label>
          密码
          <input
            v-model="password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            placeholder="至少 6 位"
          />
        </label>
        <button class="gold-btn block" type="submit" :disabled="submitting">
          {{ submitting ? "请稍候…" : mode === "login" ? "进入控制台" : "注册并进入" }}
        </button>
      </form>

      <p v-if="message" class="error">{{ message }}</p>
    </div>
  </div>
</template>

<style scoped>
.auth-wrap {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--noir);
}

.auth-card {
  position: relative;
  width: min(400px, 100%);
  padding: 40px 32px 32px;
  text-align: center;
  overflow: hidden;
}

.mark {
  width: 52px;
  height: 52px;
  margin: 0 auto 14px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  font-family: var(--serif);
  font-size: 20px;
  color: var(--gold-bright);
  background: var(--gold-soft);
}

h1 {
  margin: 0 0 4px;
  font-family: var(--serif);
  font-size: 26px;
  letter-spacing: 0.14em;
  color: var(--ivory);
}

.sub {
  margin: 0 0 6px;
  font-size: 12px;
  letter-spacing: 0.2em;
  color: var(--ivory-dim);
}

.hint {
  margin: 0 0 22px;
  font-size: 12px;
  color: var(--gold);
}

.tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 20px;
}

.tabs button {
  padding: 9px 0;
  border: 1px solid var(--line);
  border-radius: 3px;
  background: transparent;
  color: var(--ivory-dim);
  font-size: 13px;
  letter-spacing: 0.12em;
  cursor: pointer;
  transition: all 0.2s ease;
}

.tabs button.active {
  border-color: var(--line-strong);
  background: var(--gold-soft);
  color: var(--gold-bright);
}

form {
  display: grid;
  gap: 16px;
  text-align: left;
}

label {
  display: grid;
  gap: 7px;
  font-size: 12px;
  letter-spacing: 0.12em;
  color: var(--ivory-dim);
}

input {
  padding: 11px 12px;
  border: 1px solid var(--line);
  border-radius: 3px;
  background: var(--noir-2);
  color: var(--ivory);
  font-size: 14px;
  font-family: var(--sans);
}

input:focus {
  outline: none;
  border-color: var(--line-strong);
  box-shadow: 0 0 0 3px var(--gold-soft);
}

.error {
  margin: 16px 0 0;
  font-size: 13px;
  color: var(--garnet);
}
</style>
