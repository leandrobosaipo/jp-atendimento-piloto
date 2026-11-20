import {readFileSync} from 'node:fs';

const source = JSON.parse(readFileSync(new URL('./expediente-jp.json', import.meta.url), 'utf8'));
const days = ['domingo','segunda','terca','quarta','quinta','sexta','sabado'];
const modes = {manha:'morning', tarde:'afternoon', sabado:'saturday'};
const minute = time => Number(time.slice(0,2))*60 + Number(time.slice(3));
export const weeklyWindows = Object.fromEntries(days.flatMap((day,index) => {
  const ranges = source.semana[day].map(key => [...source.periodos[key].map(minute), modes[key]]);
  return ranges.length ? [[index,ranges]] : [];
}));
export const closedDates = Object.keys(source.fechamentos).sort();
export const studioTimezone = source.fuso;
if (source.limite !== 'ultimo_inicio') throw new Error('unsupported_booking_boundary');
export const boundaryDecision = Object.freeze({morning:'last_start',afternoon:'last_start',saturday:'last_start'});
