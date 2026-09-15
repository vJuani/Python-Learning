document.addEventListener("DOMContentLoaded", function () {
    var root = document.documentElement;
    var rail = document.querySelector(".app-rail.jrh-sidebar");
    var pinButton = document.querySelector("[data-sidebar-pin]");
    var desktopMq = window.matchMedia("(min-width: 1025px)");
    var hoverTimer = null;

    function isPinned() {
        return root.getAttribute("data-sidebar") === "pinned";
    }

    function setPinned(pinned) {
        if (pinned) {
            root.setAttribute("data-sidebar", "pinned");
            root.removeAttribute("data-rail");
        } else {
            root.setAttribute("data-sidebar", "collapsed");
            root.setAttribute("data-rail", "collapsed");
        }
        if (rail) {
            rail.classList.toggle("is-hover-open", false);
        }
        if (pinButton) {
            var label = pinned
                ? pinButton.getAttribute("data-label-unpin")
                : pinButton.getAttribute("data-label-pin");
            pinButton.setAttribute("aria-pressed", pinned ? "true" : "false");
            pinButton.setAttribute("aria-label", label || "");
            pinButton.setAttribute("title", label || "");
        }
        try {
            localStorage.setItem("jrh-sidebar-pinned", pinned ? "1" : "0");
        } catch (error) {
            /* ignore */
        }
    }

    function setHover(open) {
        if (!rail || !desktopMq.matches || isPinned()) {
            return;
        }
        rail.classList.toggle("is-hover-open", open);
    }

    if (pinButton) {
        pinButton.addEventListener("click", function () {
            setPinned(!isPinned());
        });
    }

    if (rail) {
        rail.addEventListener("mouseenter", function () {
            clearTimeout(hoverTimer);
            setHover(true);
        });
        rail.addEventListener("mouseleave", function () {
            clearTimeout(hoverTimer);
            hoverTimer = setTimeout(function () {
                setHover(false);
            }, 80);
        });
        rail.addEventListener("focusin", function () {
            setHover(true);
        });
        rail.addEventListener("focusout", function (event) {
            if (rail.contains(event.relatedTarget)) {
                return;
            }
            setHover(false);
        });
    }

    setPinned(isPinned());

    document.querySelectorAll("[data-nav-group]").forEach(function (group) {
        var toggle = group.querySelector(".jrh-sidebar__toggle");
        var panel = group.querySelector(".jrh-sidebar__children");
        if (!toggle || !panel) {
            return;
        }
        toggle.addEventListener("click", function () {
            var open = !group.classList.contains("is-open");
            group.classList.toggle("is-open", open);
            toggle.setAttribute("aria-expanded", open ? "true" : "false");
            panel.hidden = !open;
        });
    });

    var drawer = document.querySelector(".main-nav");
    var drawerToggle = document.querySelector(".nav-toggle");
    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape" || !drawer || !drawer.classList.contains("is-open")) {
            return;
        }
        drawer.classList.remove("is-open");
        if (drawerToggle) {
            drawerToggle.setAttribute("aria-expanded", "false");
        }
    });
});
