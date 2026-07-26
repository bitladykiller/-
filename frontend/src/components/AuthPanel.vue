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
    <div class="auth-card">
      <div class="mark" aria-hidden="true">AG</div>
      <h1>AssistGen</h1>
      <p class="sub">登录后开始对话（演示账号：demo_user / demo1234）</p>

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
        <button class="submit" type="submit" :disabled="submitting">
          {{ submitting ? "请稍候…" : mode === "login" ? "登录" : "注册并登录" }}
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
}

.auth-card {
  width: min(380px, 100%);
  padding: 32px 28px;
  border-radius: 18px;
  background: color-mix(in oklab, canvas 92%, canvastext 8%);
  border: 1px solid color-mix(in oklab, canvastext 14%, transparent);
  text-align: center;
}

.mark {
  width: 44px;
  height: 44px;
  margin: 0 auto 12px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  font-weight: 700;
  background: canvastext;
  color: canvas;
}

h1 {
  margin: 0 0 4px;
  font-size: 22px;
}

.sub {
  margin: 0 0 20px;
  font-size: 13px;
  opacity: 0.7;
}

.tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin-bottom: 18px;
}

.tabs button {
  padding: 8px 0;
  border-radius: 10px;
  border: 1px solid color-mix(in oklab, canvastext 18%, transparent);
  background: transparent;
  cursor: pointer;
}

.tabs button.active {
  background: canvastext;
  color: canvas;
  border-color: canvastext;
}

form {
  display: grid;
  gap: 14px;
  text-align: left;
}

label {
  display: grid;
  gap: 6px;
  font-size: 13px;
}

input {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid color-mix(in oklab, canvastext 20%, transparent);
  background: transparent;
  font-size: 14px;
}

.submit {
  margin-top: 4px;
  padding: 10px 0;
  border-radius: 10px;
  border: none;
  background: canvastext;
  color: canvas;
  font-weight: 600;
  cursor: pointer;
}

.submit:disabled {
  opacity: 0.6;
  cursor: wait;
}

.error {
  margin: 14px 0 0;
  font-size: 13px;
  color: #c0392b;
}
</style>
