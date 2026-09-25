const catalogFilterDisclosure = document.querySelector('.catalog-filter-disclosure');

if (
  catalogFilterDisclosure
  && window.matchMedia('(max-width: 768px)').matches
  && catalogFilterDisclosure.dataset.hasErrors !== 'true'
) {
  catalogFilterDisclosure.removeAttribute('open');
}
