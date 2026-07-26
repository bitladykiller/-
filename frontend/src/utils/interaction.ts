type InteractiveCleanup = () => void;

function resetSurface(el: HTMLElement) {
  el.style.setProperty("--tilt-rotate-x", "0deg");
  el.style.setProperty("--tilt-rotate-y", "0deg");
  el.style.setProperty("--tilt-active", "0");
}

export function bindInteractiveSurfaces(
  root: HTMLElement,
  selector = "[data-tilt]",
): InteractiveCleanup {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return () => {};
  }

  let active: HTMLElement | null = null;

  const updateRoot = (event: PointerEvent) => {
    const rect = root.getBoundingClientRect();
    root.style.setProperty("--cursor-x", `${event.clientX - rect.left}px`);
    root.style.setProperty("--cursor-y", `${event.clientY - rect.top}px`);
    root.style.setProperty("--cursor-active", "1");
  };

  const updateSurface = (surface: HTMLElement, event: PointerEvent) => {
    const rect = surface.getBoundingClientRect();
    const px = (event.clientX - rect.left) / rect.width;
    const py = (event.clientY - rect.top) / rect.height;
    const rotateY = (px - 0.5) * 10;
    const rotateX = (0.5 - py) * 10;

    surface.style.setProperty("--tilt-glow-x", `${(px * 100).toFixed(2)}%`);
    surface.style.setProperty("--tilt-glow-y", `${(py * 100).toFixed(2)}%`);
    surface.style.setProperty("--tilt-rotate-x", `${rotateX.toFixed(2)}deg`);
    surface.style.setProperty("--tilt-rotate-y", `${rotateY.toFixed(2)}deg`);
    surface.style.setProperty("--tilt-active", "1");
  };

  const onPointerMove = (event: PointerEvent) => {
    updateRoot(event);
    const target = (event.target as HTMLElement | null)?.closest(selector) as
      | HTMLElement
      | null;

    if (!target || !root.contains(target)) {
      if (active) {
        resetSurface(active);
        active = null;
      }
      return;
    }

    if (active && active !== target) {
      resetSurface(active);
    }

    active = target;
    updateSurface(target, event);
  };

  const onPointerLeave = () => {
    root.style.setProperty("--cursor-active", "0");
    if (active) {
      resetSurface(active);
      active = null;
    }
  };

  root.addEventListener("pointermove", onPointerMove);
  root.addEventListener("pointerleave", onPointerLeave);

  return () => {
    root.removeEventListener("pointermove", onPointerMove);
    root.removeEventListener("pointerleave", onPointerLeave);
    root.style.setProperty("--cursor-active", "0");
    if (active) {
      resetSurface(active);
    }
  };
}
