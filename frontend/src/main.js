import {createApp} from 'vue';
import App from './App.vue';
import './style.css';
import {pages} from './navigation.js';

if (location.hash && !Object.hasOwn(pages, location.hash)) history.replaceState(null, '', '/');
createApp(App).mount('#app');
