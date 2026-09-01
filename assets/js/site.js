(function () {
  var toggle = document.querySelector('[data-menu-toggle]');
  var navigation = document.querySelector('[data-nav-list]');

  if (!toggle || !navigation) {
    return;
  }

  toggle.addEventListener('click', function () {
    var isOpen = navigation.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', String(isOpen));
  });

  navigation.addEventListener('click', function (event) {
    if (event.target.tagName !== 'A') {
      return;
    }

    navigation.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
  });
}());
