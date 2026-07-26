<template>
  <div class="atmosphere" aria-hidden="true">
    <div class="aurora aurora-a" />
    <div class="aurora aurora-b" />
    <div class="aurora aurora-c" />
    <div class="lattice" />
    <div class="vignette" />
  </div>
  <router-view />
</template>

<style scoped>
/* 全局氛围层：极光金雾 + 菱格暗纹 + 边缘暗角，置于所有视图之下 */
.atmosphere {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
}

.aurora {
  position: absolute;
  border-radius: 50%;
}

.aurora-a {
  width: 58vw;
  height: 58vw;
  right: -14vw;
  top: -24vw;
  background: radial-gradient(circle, rgba(216, 178, 106, 0.13), transparent 62%);
  animation: aurora-drift 34s ease-in-out infinite alternate;
}

.aurora-b {
  width: 52vw;
  height: 52vw;
  left: -18vw;
  bottom: -26vw;
  background: radial-gradient(circle, rgba(142, 201, 168, 0.085), transparent 62%);
  animation: aurora-drift 42s ease-in-out infinite alternate-reverse;
}

.aurora-c {
  width: 30vw;
  height: 30vw;
  left: 22vw;
  top: 8vh;
  background: radial-gradient(circle, rgba(216, 178, 106, 0.05), transparent 60%);
  animation: aurora-drift 48s ease-in-out infinite alternate;
}

.lattice {
  position: absolute;
  inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='56' height='56'%3E%3Cpath d='M28 0L56 28L28 56L0 28Z' fill='none' stroke='%23d8b26a' stroke-opacity='0.05' stroke-width='1'/%3E%3C/svg%3E");
  background-size: 56px 56px;
  -webkit-mask-image: radial-gradient(120% 100% at 50% 0%, #000 30%, transparent 90%);
  mask-image: radial-gradient(120% 100% at 50% 0%, #000 30%, transparent 90%);
}

.vignette {
  position: absolute;
  inset: 0;
  background: radial-gradient(130% 130% at 50% 38%, transparent 55%, rgba(0, 0, 0, 0.52));
}
</style>
