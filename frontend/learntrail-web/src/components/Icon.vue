<script setup>
defineProps({ name: { type: String, default: 'arrow' }, size: { type: [Number, String], default: 20 } })
const paths = {
  discover: ['M12 3l3 6 6 3-6 3-3 6-3-6-6-3 6-3z'],
  play: ['M9 5l11 7-11 7z'],
  library: ['M4 4h5v16H4z', 'M12 4h5v16h-5z', 'M20 5l2 14'],
  bookmark: ['M6 3h12v18l-6-4-6 4z'],
  creators: ['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2', 'M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8', 'M17 3a4 4 0 0 1 0 8', 'M22 21v-2a4 4 0 0 0-3-3.9'],
  search: ['M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15', 'M16 16l5 5'],
  arrow: ['M4 12h16', 'M14 6l6 6-6 6'],
  upRight: ['M6 18L18 6', 'M6 6h12v12'],
  chevron: ['M9 5l7 7-7 7'],
  back: ['M20 12H4', 'M10 6l-6 6 6 6'],
  lock: ['M6 10h12v11H6z', 'M8 10V7a4 4 0 0 1 8 0v3'],
  check: ['M4 12l5 5L20 6'],
  settings: ['M4 7h16', 'M4 17h16', 'M8 4v6', 'M16 14v6'],
  logout: ['M9 4H4v16h5', 'M12 12h9', 'M17 8l4 4-4 4'],
  headphones: ['M3 14v-2a9 9 0 0 1 18 0v2', 'M3 13h4v8H3z', 'M17 13h4v8h-4z'],
  menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  close: ['M5 5l14 14', 'M19 5L5 19'],
  clock: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18', 'M12 7v5l3 2'],
  heart: ['M20.8 4.6a5.4 5.4 0 0 0-7.6 0L12 5.8l-1.2-1.2a5.4 5.4 0 0 0-7.6 7.6L12 21l8.8-8.8a5.4 5.4 0 0 0 0-7.6z'],
  plus: ['M12 4v16', 'M4 12h16'],
  monitor: ['M3 4h18v13H3z', 'M8 21h8', 'M12 17v4'],
  bag: ['M4 7h16l-1 14H5z', 'M8 7V5a4 4 0 0 1 8 0v2'],
  grid: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z']
}
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path v-for="(d, i) in (paths[name] || paths.arrow)" :key="i" :d="d" />
  </svg>
</template>
