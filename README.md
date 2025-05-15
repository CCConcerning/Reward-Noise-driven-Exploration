# Reward Noise-driven Exploration for Deep Reinforcement Learning

This is the codebase for "Uncertain Reward bring more possibility: Reward Noise-driven Exploration for Deep Reinforcement Learning". 


## Installation

### Installing IsaacGym

Follow the instructions in the [IsaacGymEnvs](https://github.com/isaac-sim/IsaacGymEnvs) repository to setup IsaacGym.


### Installing IsaacGymEnvs

Once IsaacGym is installed and you are able to successfully run the examples, install IsaacGymEnvs:

```
cd isaacgym/IsaacGymEnvs
pip install -e .
```

### Install other requirements

```
pip install -r requirements.txt
```

### Running Experiments
To run SrRN in Atari games, run the following:
```
python atari/ppo_atari_envpool.py --add_noise True --bi_noise False --rate 0.5
```
To run BiRN in Atari games, run the following:
```
python atari/ppo_atari_envpool.py --add_noise True --bi_noise True
```

To run SrRN in IsaacGymEnvs tasks, run the following:
```
python atari/ppo_norm_rew.py --add_noise True --bi_noise False --rate 0.5
```

To run BiRN in IsaacGymEnvs tasks, run the following:
```
python atari/ppo_norm_rew.py --add_noise True --bi_noise True
```
## Acknowledgements

We thank [CleanRL](https://github.com/vwxyzjn/cleanrl) and [IsaacGymEnvs](https://github.com/isaac-sim/IsaacGymEnvs) for their amazing work, which were instrumental in making this work possible.


