/**
 * Advisory layer client-side behavior.
 * Handles form loading states and progressive enhancement.
 */
document.addEventListener('DOMContentLoaded', function () {
    // Add loading state to form submissions
    document.querySelectorAll('form').forEach(function (form) {
        form.addEventListener('submit', function () {
            var btn = form.querySelector('button[type="submit"]');
            if (btn) {
                btn.disabled = true;
                var originalHTML = btn.innerHTML;
                btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Processing...';
                // Re-enable after 10s in case of redirect issues
                setTimeout(function () {
                    btn.disabled = false;
                    btn.innerHTML = originalHTML;
                }, 10000);
            }
        });
    });
});
