import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {SprigFilm} from './SprigFilm';

const Root = () => <Composition id="Sprig-Product-Film" component={SprigFilm}
  durationInFrames={1440} fps={60} width={1080} height={1350} />;

registerRoot(Root);
