document.addEventListener("DOMContentLoaded", () => {
    gsap.from("div", { opacity: 0, y: 50, duration: 1, ease: "power3.out" });
    gsap.from("input, button", { opacity: 0, scale: 0.8, stagger: 0.2, duration: 0.5, delay: 0.5 });
});