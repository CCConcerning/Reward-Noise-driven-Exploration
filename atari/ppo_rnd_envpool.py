# docs and experiment results can be found at https://docs.cleanrl.dev/rl-algorithms/ppo-rnd/#ppo_rnd_envpoolpy
import argparse
import os
import random
import time
from collections import deque
from distutils.util import strtobool
import matplotlib.pyplot as plt
import seaborn as sns
from copy import deepcopy

import envpool
import gym
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from gym.wrappers.normalize import RunningMeanStd
from gym.wrappers.record_video import RecordVideo
from torch.distributions.categorical import Categorical

import csv
import gc

def parse_args():
    # fmt: off
    parser = argparse.ArgumentParser()
    parser.add_argument("--exp-name", type=str, default=os.path.basename(__file__).rstrip(".py"),
        help="the name of this experiment")
    parser.add_argument("--seed", type=int, default=1,
        help="seed of the experiment")
    parser.add_argument("--torch-deterministic", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, `torch.backends.cudnn.deterministic=False`")
    parser.add_argument("--cuda", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, cuda will be enabled by default")
    parser.add_argument("--track", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, this experiment will be tracked with Weights and Biases")
    parser.add_argument("--wandb-project-name", type=str, default="randomized-exploration",
        help="the wandb's project name")
    parser.add_argument("--wandb-entity", type=str, default="",
        help="the entity (team) of wandb's project")
    parser.add_argument("--capture-video", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
        help="whether to capture videos of the agent performances (log it on wandb)")
    parser.add_argument("--capture-video-interval", type=int, default=10,
        help="How many training updates to wait before capturing video")
    parser.add_argument("--gpu-id", type=int, default=0,
        help="ID of GPU to use")

    # Algorithm specific arguments
    parser.add_argument("--env-id", type=str, default="Amidar-v5",
        help="the id of the environment")
    parser.add_argument("--total-timesteps", type=int, default=40000000,
        help="total timesteps of the experiments")
    parser.add_argument("--learning-rate", type=float, default=3e-4,
        help="the learning rate of the optimizer")
    parser.add_argument("--num-envs", type=int, default=128,
        help="the number of parallel game environments")
    parser.add_argument("--num-steps", type=int, default=128,
        help="the number of steps to run in each environment per policy rollout")
    parser.add_argument("--anneal-lr", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
        help="Toggle learning rate annealing for policy and value networks")
    parser.add_argument("--gamma", type=float, default=0.999,
        help="the discount factor gamma")
    parser.add_argument("--gae-lambda", type=float, default=0.95,
        help="the lambda for the general advantage estimation")
    parser.add_argument("--num-minibatches", type=int, default=4,
        help="the number of mini-batches")
    parser.add_argument("--update-epochs", type=int, default=4,
        help="the K epochs to update the policy")
    parser.add_argument("--norm-adv", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="Toggles advantages normalization")
    parser.add_argument("--clip-coef", type=float, default=0.1,
        help="the surrogate clipping coefficient")
    parser.add_argument("--clip-vloss", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="Toggles whether or not to use a clipped loss for the value function, as per the paper.")
    parser.add_argument("--ent-coef", type=float, default=0.001,
        help="coefficient of the entropy")
    parser.add_argument("--vf-coef", type=float, default=0.5,
        help="coefficient of the value function")
    parser.add_argument("--max-grad-norm", type=float, default=0.5,
        help="the maximum norm for the gradient clipping")
    parser.add_argument("--target-kl", type=float, default=None,
        help="the target KL divergence threshold")
    parser.add_argument("--sticky-action", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, sticky action will be used")
    parser.add_argument("--normalize-ext-rewards", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, extrinsic rewards will be normalized")
    
    # Evaluation specific arguments
    parser.add_argument("--eval-interval", type=int, default=0,
        help="number of epochs between evaluations (0 to skip)")
    parser.add_argument("--num-eval-envs", type=int, default=32,
        help="the number of evaluation environments")
    parser.add_argument("--num-eval-episodes", type=int, default=32,
        help="the number of episodes to evaluate with")

    # RND arguments
    parser.add_argument("--update-proportion", type=float, default=0.25,
        help="proportion of exp used for predictor update")
    parser.add_argument("--int-coef", type=float, default=1.0,
        help="coefficient of extrinsic reward")
    parser.add_argument("--ext-coef", type=float, default=2.0,
        help="coefficient of intrinsic reward")
    parser.add_argument("--int-gamma", type=float, default=0.99,
        help="Intrinsic reward discount rate")
    parser.add_argument("--num-iterations-obs-norm-init", type=int, default=50,
        help="number of iterations to initialize the observations normalization parameters")
    parser.add_argument("--save_model", type=bool, default=True, help="if save model")
    args = parser.parse_args()
    args.batch_size = int(args.num_envs * args.num_steps)
    args.minibatch_size = int(args.batch_size // args.num_minibatches)
    args.add_noise = False
    # fmt: on
    return args


class RecordEpisodeStatistics(gym.Wrapper):
    def __init__(self, env, deque_size=100):
        super().__init__(env)
        self.num_envs = getattr(env, "num_envs", 1)
        self.episode_returns = None
        self.episode_lengths = None

    def reset(self, **kwargs):
        observations = super().reset(**kwargs)
        self.episode_returns = np.zeros(self.num_envs, dtype=np.float32)
        self.episode_lengths = np.zeros(self.num_envs, dtype=np.int32)
        self.lives = np.zeros(self.num_envs, dtype=np.int32)
        self.returned_episode_returns = np.zeros(self.num_envs, dtype=np.float32)
        self.returned_episode_lengths = np.zeros(self.num_envs, dtype=np.int32)
        return observations

    def step(self, action):
        observations, rewards, dones, infos = super().step(action)
        self.episode_returns += infos["reward"]
        self.episode_lengths += 1
        self.returned_episode_returns[:] = self.episode_returns
        self.returned_episode_lengths[:] = self.episode_lengths
        self.episode_returns *= 1 - (infos["terminated"] | infos["TimeLimit.truncated"])
        self.episode_lengths *= 1 - (infos["terminated"] | infos["TimeLimit.truncated"])
        infos["r"] = self.returned_episode_returns
        infos["l"] = self.returned_episode_lengths
        return (
            observations,
            rewards,
            dones,
            infos,
        )


# ALGO LOGIC: initialize agent here:
def layer_init(layer, std=np.sqrt(2), bias_const=0.0):
    torch.nn.init.orthogonal_(layer.weight, std)
    torch.nn.init.constant_(layer.bias, bias_const)
    return layer


class Agent(nn.Module):
    def __init__(self, envs):
        super().__init__()
        self.network = nn.Sequential(
            layer_init(nn.Conv2d(4, 32, 8, stride=4)),
            nn.ReLU(),
            layer_init(nn.Conv2d(32, 64, 4, stride=2)),
            nn.ReLU(),
            layer_init(nn.Conv2d(64, 64, 3, stride=1)),
            nn.ReLU(),
            nn.Flatten(),
            layer_init(nn.Linear(64 * 7 * 7, 256)),
            nn.ReLU(),
            layer_init(nn.Linear(256, 448)),
            nn.ReLU(),
        )
        self.extra_layer = nn.Sequential(layer_init(nn.Linear(448, 448), std=0.1), nn.ReLU())
        self.actor = nn.Sequential(
            layer_init(nn.Linear(448, 448), std=0.01),
            nn.ReLU(),
            layer_init(nn.Linear(448, envs.single_action_space.n), std=0.01),
        )
        self.critic_ext = layer_init(nn.Linear(448, 1), std=0.01)
        self.critic_int = layer_init(nn.Linear(448, 1), std=0.01)

    def get_action_and_value(self, x, action=None, deterministic=False):
        hidden = self.network(x / 255.0)
        logits = self.actor(hidden)
        probs = Categorical(logits=logits)
        features = self.extra_layer(hidden)
        if action is None and not deterministic:
            action = probs.sample()
        elif action is None and deterministic:
            action = probs.probs.argmax(dim=1)
        return (
            action,
            probs.log_prob(action),
            probs.entropy(),
            self.critic_ext(features + hidden),
            self.critic_int(features + hidden),
        )

    def get_value(self, x):
        hidden = self.network(x / 255.0)
        features = self.extra_layer(hidden)
        return self.critic_ext(features + hidden), self.critic_int(features + hidden)


class RNDModel(nn.Module):
    def __init__(self, input_size, output_size):
        super().__init__()

        self.input_size = input_size
        self.output_size = output_size

        feature_output = 7 * 7 * 64

        # Prediction network
        self.predictor = nn.Sequential(
            layer_init(nn.Conv2d(in_channels=1, out_channels=32, kernel_size=8, stride=4)),
            nn.LeakyReLU(),
            layer_init(nn.Conv2d(in_channels=32, out_channels=64, kernel_size=4, stride=2)),
            nn.LeakyReLU(),
            layer_init(nn.Conv2d(in_channels=64, out_channels=64, kernel_size=3, stride=1)),
            nn.LeakyReLU(),
            nn.Flatten(),
            layer_init(nn.Linear(feature_output, 512)),
            nn.ReLU(),
            layer_init(nn.Linear(512, 512)),
            nn.ReLU(),
            layer_init(nn.Linear(512, 512)),
        )

        # Target network
        self.target = nn.Sequential(
            layer_init(nn.Conv2d(in_channels=1, out_channels=32, kernel_size=8, stride=4)),
            nn.LeakyReLU(),
            layer_init(nn.Conv2d(in_channels=32, out_channels=64, kernel_size=4, stride=2)),
            nn.LeakyReLU(),
            layer_init(nn.Conv2d(in_channels=64, out_channels=64, kernel_size=3, stride=1)),
            nn.LeakyReLU(),
            nn.Flatten(),
            layer_init(nn.Linear(feature_output, 512)),
        )

        # target network is not trainable
        for param in self.target.parameters():
            param.requires_grad = False

    def forward(self, next_obs):
        target_feature = self.target(next_obs)
        predict_feature = self.predictor(next_obs)

        return predict_feature, target_feature


class RewardForwardFilter:
    def __init__(self, gamma):
        self.rewems = None
        self.gamma = gamma

    def update(self, rews, not_done=None):
        if not_done is None:
            if self.rewems is None:
                self.rewems = rews
            else:
                self.rewems = self.rewems * self.gamma + rews
            return self.rewems
        else:
            if self.rewems is None:
                self.rewems = rews
            else:
                mask = np.where(not_done == 1.0)
                self.rewems[mask] = self.rewems[mask] * self.gamma + rews[mask]
            return deepcopy(self.rewems)


if __name__ == "__main__":
    args = parse_args()
    run_name = f"{args.env_id}__{args.exp_name}__{args.seed}_lr{args.learning_rate}_{int(time.time())}"
    if args.track:
        import wandb

        wandb.init(
            project=args.wandb_project_name,
            entity=args.wandb_entity,
            # sync_tensorboard=True,
            config=vars(args),
            name=run_name,
            # monitor_gym=True,
            save_code=True,
        )
    if args.save_model:
        log_path = f"results/{run_name}"
        model_path = f"results/{run_name}/models"
        if not os.path.exists(model_path):
            os.makedirs(model_path, exist_ok=False)
        csv_filename = os.path.join(log_path, '{}.csv').format(run_name)

    # writer = SummaryWriter(f"runs/{run_name}")
    # writer.add_text(
    #     "hyperparameters",
    #     "|param|value|\n|-|-|\n%s" % ("\n".join([f"|{key}|{value}|" for key, value in vars(args).items()])),
    # )

    # TRY NOT TO MODIFY: seeding
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.backends.cudnn.deterministic = args.torch_deterministic

    device = torch.device(f"cuda:{args.gpu_id}" if torch.cuda.is_available() and args.cuda else "cpu")

    # env setup
    envs = envpool.make(
        args.env_id,
        env_type="gym",
        num_envs=args.num_envs,
        episodic_life=True,
        reward_clip=True,
        max_episode_steps=int(108000 / 4),
        seed=args.seed,
        repeat_action_probability=0.25,
    )
    envs.num_envs = args.num_envs
    envs.single_action_space = envs.action_space
    envs.single_observation_space = envs.observation_space
    envs = RecordEpisodeStatistics(envs)
    # setattr(envs, "is_vector_env", True)
    # envs = RecordVideo(envs, video_folder=f"videos/{run_name}")
    assert isinstance(envs.action_space, gym.spaces.Discrete), "only discrete action space is supported"

    agent = Agent(envs).to(device)
    rnd_model = RNDModel(4, envs.single_action_space.n).to(device)
    combined_parameters = list(agent.parameters()) + list(rnd_model.predictor.parameters())
    optimizer = optim.Adam(
        combined_parameters,
        lr=args.learning_rate,
        eps=1e-5,
    )

    obs_rms = RunningMeanStd(shape=(1, 1, 84, 84))
    int_reward_rms = RunningMeanStd()
    int_discounted_reward = RewardForwardFilter(args.int_gamma)
    ext_reward_rms = RunningMeanStd()
    ext_discounted_reward = RewardForwardFilter(args.gamma)

    # ALGO Logic: Storage setup
    obs = torch.zeros((args.num_steps, args.num_envs) + envs.single_observation_space.shape).to(device)
    actions = torch.zeros((args.num_steps, args.num_envs) + envs.single_action_space.shape).to(device)
    logprobs = torch.zeros((args.num_steps, args.num_envs)).to(device)
    rewards = torch.zeros((args.num_steps, args.num_envs)).to(device)
    curiosity_rewards = torch.zeros((args.num_steps, args.num_envs)).to(device)
    dones = torch.zeros((args.num_steps, args.num_envs)).to(device)
    ext_values = torch.zeros((args.num_steps, args.num_envs)).to(device)
    int_values = torch.zeros((args.num_steps, args.num_envs)).to(device)
    avg_returns = deque(maxlen=128)
    avg_ep_lens = deque(maxlen=128)

    # TRY NOT TO MODIFY: start the game
    global_step = 0
    start_time = time.time()
    next_obs = torch.Tensor(envs.reset()).to(device)
    next_done = torch.zeros(args.num_envs).to(device)
    num_updates = args.total_timesteps // args.batch_size

    video_filenames = set()
    
    print("Start to initialize observation normalization parameter.....")
    next_ob = []
    for step in range(args.num_steps * args.num_iterations_obs_norm_init):
        acs = np.random.randint(0, envs.single_action_space.n, size=(args.num_envs,))
        s, r, d, _ = envs.step(acs)
        next_ob += s[:, 3, :, :].reshape([-1, 1, 84, 84]).tolist()

        if len(next_ob) % (args.num_steps * args.num_envs) == 0:
            next_ob = np.stack(next_ob)
            obs_rms.update(next_ob)
            next_ob = []
    print(f"End to initialize... finished in {time.time() - start_time}")

    record_video = False # True if need to start recording when next episode finishes
    recording = True # True if currently recording
    log_recorded_video = False # True if video recorded and needs to be logged at end of episode
    frames = []
    recorded_intrinsic_rews = []
    for update in range(1, num_updates + 1):
        it_start_time = time.time()

        if args.track and args.capture_video and update % args.capture_video_interval==0 and not recording:
            record_video = True
        # Annealing the rate if instructed to do so.
        if args.anneal_lr:
            frac = 1.0 - (update - 1.0) / num_updates
            lrnow = frac * args.learning_rate
            optimizer.param_groups[0]["lr"] = lrnow

        for step in range(0, args.num_steps):
            global_step += 1 * args.num_envs
            obs[step] = next_obs
            dones[step] = next_done

            if recording:
                frames += [next_obs[0,3,:,:].cpu()]

            # ALGO LOGIC: action logic
            with torch.no_grad():
                value_ext, value_int = agent.get_value(obs[step])
                ext_values[step], int_values[step] = (
                    value_ext.flatten(),
                    value_int.flatten(),
                )
                action, logprob, _, _, _ = agent.get_action_and_value(obs[step])

            actions[step] = action
            logprobs[step] = logprob

            # TRY NOT TO MODIFY: execute the game and log data.
            next_obs, reward, done, info = envs.step(action.cpu().numpy())
            rewards[step] = torch.tensor(reward).to(device).view(-1)
            next_obs, next_done = torch.Tensor(next_obs).to(device), torch.Tensor(done).to(device)
            rnd_next_obs = (
                (
                    (next_obs[:, 3, :, :].reshape(args.num_envs, 1, 84, 84) - torch.from_numpy(obs_rms.mean).to(device))
                    / torch.sqrt(torch.from_numpy(obs_rms.var).to(device))
                ).clip(-5, 5)
            ).float()
            target_next_feature = rnd_model.target(rnd_next_obs)
            predict_next_feature = rnd_model.predictor(rnd_next_obs)
            curiosity_rewards[step] = ((target_next_feature - predict_next_feature).pow(2).sum(1) / 2).data
            if recording:
                recorded_intrinsic_rews += [curiosity_rewards[step][0].item()]
                if info["terminated"][0] or info["TimeLimit.truncated"][0]:
                    recording = False
                    log_recorded_video = True
            elif record_video and (info["terminated"][0] or info["TimeLimit.truncated"][0]):
                record_video = False
                recording = True
                print("RECORDING VIDEO...")

            for idx, d in enumerate(done):
                if info["terminated"][idx] or info["TimeLimit.truncated"][idx]:
                    avg_returns.append(info["r"][idx])
                    avg_ep_lens.append(info["l"][idx])

        not_dones = (1.0 - dones).cpu().data.numpy()
        rewards_cpu = rewards.cpu().data.numpy()
        curiosity_rewards_cpu = curiosity_rewards.cpu().data.numpy()
        if args.normalize_ext_rewards:
            ext_reward_per_env = np.array(
                [ext_discounted_reward.update(rewards_cpu[i], not_dones[i]) for i in range(args.num_steps)]
            )
            ext_reward_rms.update(ext_reward_per_env.flatten())
            rewards /= np.sqrt(ext_reward_rms.var)

        if True:
            curiosity_reward_per_env = np.array(
                [int_discounted_reward.update(curiosity_rewards_cpu[i], not_dones[i]) for i in range(args.num_steps)]
            )
            int_reward_rms.update(curiosity_reward_per_env.flatten())
            curiosity_rewards /= np.sqrt(int_reward_rms.var)

        # bootstrap value if not done
        with torch.no_grad():
            next_value_ext, next_value_int = agent.get_value(next_obs)
            next_value_ext, next_value_int = next_value_ext.reshape(1, -1), next_value_int.reshape(1, -1)
            ext_advantages = torch.zeros_like(rewards, device=device)
            int_advantages = torch.zeros_like(curiosity_rewards, device=device)
            ext_lastgaelam = 0
            int_lastgaelam = 0
            for t in reversed(range(args.num_steps)):
                if t == args.num_steps - 1:
                    ext_nextnonterminal = 1.0 - next_done
                    int_nextnonterminal = 1.0
                    ext_nextvalues = next_value_ext
                    int_nextvalues = next_value_int
                else:
                    ext_nextnonterminal = 1.0 - dones[t + 1]
                    int_nextnonterminal = 1.0
                    ext_nextvalues = ext_values[t + 1]
                    int_nextvalues = int_values[t + 1]
                ext_delta = rewards[t] + args.gamma * ext_nextvalues * ext_nextnonterminal - ext_values[t]
                int_delta = curiosity_rewards[t] + args.int_gamma * int_nextvalues * int_nextnonterminal - int_values[t]
                ext_advantages[t] = ext_lastgaelam = (
                    ext_delta + args.gamma * args.gae_lambda * ext_nextnonterminal * ext_lastgaelam
                )
                int_advantages[t] = int_lastgaelam = (
                    int_delta + args.int_gamma * args.gae_lambda * int_nextnonterminal * int_lastgaelam
                )
            ext_returns = ext_advantages + ext_values
            int_returns = int_advantages + int_values

        # flatten the batch
        b_obs = obs.reshape((-1,) + envs.single_observation_space.shape)
        b_logprobs = logprobs.reshape(-1)
        b_actions = actions.reshape(-1)
        b_ext_advantages = ext_advantages.reshape(-1)
        b_int_advantages = int_advantages.reshape(-1)
        b_ext_returns = ext_returns.reshape(-1)
        b_int_returns = int_returns.reshape(-1)
        b_ext_values = ext_values.reshape(-1)
        b_int_values = int_values.reshape(-1)

        b_advantages = b_int_advantages * args.int_coef + b_ext_advantages * args.ext_coef

        obs_rms.update(b_obs[:, 3, :, :].reshape(-1, 1, 84, 84).cpu().numpy())

        # Optimizing the policy and value network
        b_inds = np.arange(args.batch_size)

        rnd_next_obs = (
            (
                (b_obs[:, 3, :, :].reshape(-1, 1, 84, 84) - torch.from_numpy(obs_rms.mean).to(device))
                / torch.sqrt(torch.from_numpy(obs_rms.var).to(device))
            ).clip(-5, 5)
        ).float()

        clipfracs = []
        for epoch in range(args.update_epochs):
            np.random.shuffle(b_inds)
            for start in range(0, args.batch_size, args.minibatch_size):
                end = start + args.minibatch_size
                mb_inds = b_inds[start:end]

                predict_next_state_feature, target_next_state_feature = rnd_model(rnd_next_obs[mb_inds])
                forward_loss = F.mse_loss(
                    predict_next_state_feature, target_next_state_feature.detach(), reduction="none"
                ).mean(-1)

                mask = torch.rand(len(forward_loss), device=device)
                mask = (mask < args.update_proportion).type(torch.FloatTensor).to(device)
                forward_loss = (forward_loss * mask).sum() / torch.max(
                    mask.sum(), torch.tensor([1], device=device, dtype=torch.float32)
                )
                _, newlogprob, entropy, new_ext_values, new_int_values = agent.get_action_and_value(
                    b_obs[mb_inds], b_actions.long()[mb_inds]
                )
                logratio = newlogprob - b_logprobs[mb_inds]
                ratio = logratio.exp()

                with torch.no_grad():
                    # calculate approx_kl http://joschu.net/blog/kl-approx.html
                    old_approx_kl = (-logratio).mean()
                    approx_kl = ((ratio - 1) - logratio).mean()
                    clipfracs += [((ratio - 1.0).abs() > args.clip_coef).float().mean().item()]

                mb_advantages = b_advantages[mb_inds]
                if args.norm_adv:
                    mb_advantages = (mb_advantages - mb_advantages.mean()) / (mb_advantages.std() + 1e-8)

                # Policy loss
                pg_loss1 = -mb_advantages * ratio
                pg_loss2 = -mb_advantages * torch.clamp(ratio, 1 - args.clip_coef, 1 + args.clip_coef)
                pg_loss = torch.max(pg_loss1, pg_loss2).mean()

                # Value loss
                new_ext_values, new_int_values = new_ext_values.view(-1), new_int_values.view(-1)
                if args.clip_vloss:
                    ext_v_loss_unclipped = (new_ext_values - b_ext_returns[mb_inds]) ** 2
                    ext_v_clipped = b_ext_values[mb_inds] + torch.clamp(
                        new_ext_values - b_ext_values[mb_inds],
                        -args.clip_coef,
                        args.clip_coef,
                    )
                    ext_v_loss_clipped = (ext_v_clipped - b_ext_returns[mb_inds]) ** 2
                    ext_v_loss_max = torch.max(ext_v_loss_unclipped, ext_v_loss_clipped)
                    ext_v_loss = 0.5 * ext_v_loss_max.mean()
                else:
                    ext_v_loss = 0.5 * ((new_ext_values - b_ext_returns[mb_inds]) ** 2).mean()

                int_v_loss = 0.5 * ((new_int_values - b_int_returns[mb_inds]) ** 2).mean()
                v_loss = ext_v_loss + int_v_loss
                entropy_loss = entropy.mean()
                loss = pg_loss - args.ent_coef * entropy_loss + v_loss * args.vf_coef + forward_loss

                optimizer.zero_grad()
                loss.backward()
                if args.max_grad_norm:
                    nn.utils.clip_grad_norm_(
                        combined_parameters,
                        args.max_grad_norm,
                    )
                optimizer.step()

            if args.target_kl is not None:
                if approx_kl > args.target_kl:
                    break

        it_end_time = time.time()


        if args.eval_interval != 0 and update % args.eval_interval == 0:
            print(f"Evaluating at step {update}...")
            # Evaluate the agent by taking actions from the deterministic policy with goal vector = 0
            eval_start_time = time.time()
            eval_scores = []
            eval_ep_lens = []
            # Create eval envs
            eval_envs = envpool.make(
                args.env_id,
                env_type="gym",
                num_envs=args.num_eval_envs, # keep this small to save memory
                episodic_life=True,
                reward_clip=True,
                max_episode_steps=int(108000 / 4),
                seed=args.seed,
                repeat_action_probability=0.25,
            )
            eval_envs.num_envs = args.num_eval_envs
            eval_envs.single_action_space = eval_envs.action_space
            eval_envs.single_observation_space = eval_envs.observation_space
            eval_envs = RecordEpisodeStatistics(eval_envs)

            # Evaluate the agent
            eval_obs = torch.Tensor(eval_envs.reset()).to(device)
            eval_done = torch.zeros(args.num_eval_envs).to(device)

            # Rollout the environments until the number of completed episodes is equal to the number of evaluation environments
            while len(eval_scores) < args.num_eval_episodes:
                # Sample actions from the policy
                with torch.no_grad():
                    eval_action, _, _, _, _ = agent.get_action_and_value(
                        eval_obs, deterministic=True
                    )
                eval_obs, eval_reward, eval_done, eval_info = eval_envs.step(eval_action.cpu().numpy())
                eval_reward = torch.tensor(eval_reward).to(device).view(-1)
                eval_obs, eval_done = torch.Tensor(eval_obs).to(device), torch.Tensor(eval_done).to(device)

                for idx, d in enumerate(eval_done):
                    if eval_info["terminated"][idx] or eval_info["TimeLimit.truncated"][idx]:
                        eval_scores.append(eval_info["r"][idx])
                        eval_ep_lens.append(eval_info["elapsed_step"][idx])
                        
            eval_envs.close()
            eval_end_time = time.time()

            print(f"Evaluation finished in {eval_end_time - eval_start_time} seconds")
            print(f"Step {update}: game score: {np.mean(eval_scores)}")

            eval_data = {}
            eval_data["eval/score"] = np.mean(eval_scores)
            eval_data["eval/min_score"] = np.min(eval_scores)
            eval_data["eval/max_score"] = np.max(eval_scores)
            eval_data["eval/ep_len"] = np.mean(eval_ep_lens)
            eval_data["eval/min_ep_len"] = np.min(eval_ep_lens)
            eval_data["eval/max_ep_len"] = np.max(eval_ep_lens)
            eval_data["eval/num_episodes"] = len(eval_scores)
            eval_data["eval/time"] = eval_end_time - eval_start_time

            if args.track:
                wandb.log(eval_data, step=global_step)
                print("LOGGED")


        print("SPS:", int(global_step / (time.time() - start_time)))
        # writer.add_scalar("charts/SPS", int(global_step / (time.time() - start_time)), global_step)

        data = {}
        data["charts/iterations"] = update
        data["charts/learning_rate"] = optimizer.param_groups[0]["lr"]
        data["losses/ext_value_loss"] = ext_v_loss.item()
        data["losses/int_value_loss"] = int_v_loss.item()
        data["losses/policy_loss"] = pg_loss.item()
        data["losses/entropy"] = entropy_loss.item()
        data["losses/old_approx_kl"] = old_approx_kl.item()
        data["losses/clipfrac"] = np.mean(clipfracs)
        # data["losses/explained_ext_var"] = np.mean(explained_ext_var)
        # data["losses/explained_int_var"] = np.mean(explained_int_var)
        data["losses/fwd_loss"] = forward_loss.item()
        data["losses/approx_kl"] = approx_kl.item()
        data["losses/all_loss"] = loss.item()
        data["charts/SPS"] = int(global_step / (time.time() - start_time))
        data["rewards/rewards_mean"] = rewards.mean().item()
        data["rewards/rewards_max"] = rewards.max().item()
        data["rewards/rewards_min"] = rewards.min().item()
        data["rewards/intrinsic_rewards_mean"] = curiosity_rewards.mean().item()
        data["rewards/intrinsic_rewards_max"] = curiosity_rewards.max().item()
        data["rewards/intrinsic_rewards_min"] = curiosity_rewards.min().item()


        # Log the number of envs with positive extrinsic rewards (rewards has shape (num_steps, num_envs))
        data["rewards/num_envs_with_pos_rews"] = torch.sum(rewards.sum(dim=0) > 0).item()

        # Log advantages and intrinsic advantages
        data["returns/advantages"] = b_advantages.mean().item()
        data["returns/ext_advantages"] = b_ext_advantages.mean().item()
        data["returns/int_advantages"] = b_int_advantages.mean().item()
        data["returns/ret_ext"] = b_ext_returns.mean().item()
        data["returns/ret_int"] = b_int_returns.mean().item()
        data["returns/values_ext"] = b_ext_values.mean().item()
        data["returns/values_int"] = b_int_values.mean().item()

        data["charts/traj_len"] = np.mean(avg_ep_lens)
        data["charts/max_traj_len"] = np.max(avg_ep_lens, initial=0)
        data["charts/min_traj_len"] = np.min(avg_ep_lens, initial=0)
        data["charts/time_per_it"] = it_end_time - it_start_time
        data["charts/game_score"] = np.mean(avg_returns)
        data["charts/max_game_score"] = np.max(avg_returns, initial=0)
        data["charts/min_game_score"] = np.min(avg_returns, initial=0)

        print(f"Iteration {update} complete")

        if args.track:
            wandb.log(data, step=global_step)
            with open(csv_filename, 'a', newline='') as csvfile:
                writer = csv.writer(csvfile)
                # if not file_exists:
                #     writer.writeheader()  # 如果文件不存在，写入头部
                writer.writerow((global_step, data["charts/game_score"], data["rewards/rewards_mean"], data["charts/traj_len"], data["losses/entropy"]))  # 写入数据行

        if args.track and args.capture_video and log_recorded_video:
            # frames is a list of images of (84,84) - need to expand dims to make the grayscale loggable by wandb
            video_array = np.expand_dims(np.concatenate([np.expand_dims(frame.cpu().numpy(), axis=0) for frame in frames], axis=0), axis=1)
            print(f"LOGGED VIDEO... {video_array.shape}")
            wandb.log({"video/obs": wandb.Video(video_array, fps=30)}, step=global_step)
            # Log curiosity rewards
            fig = plt.figure()
            intrinsic_lineplot = sns.lineplot(recorded_intrinsic_rews)
            log_data = wandb.Image(fig)
            wandb.log({"int_rewards_plot/rewards": log_data}, step=global_step)
            plt.clf()
            # Compute and log cumulative sum of intrinsic rewards
            cumulative_sum = np.cumsum(recorded_intrinsic_rews)
            cumulative_lineplot = sns.lineplot(cumulative_sum)
            log_data = wandb.Image(fig)
            wandb.log({"int_rewards_plot/cumulative_rewards": log_data}, step=global_step)
            plt.clf()
            # Compute and log discounted cumulative sum of intrinsic rewards (use args.int_gamma)
            # We want: gamma^0*r_0 + ... + gamma^n*r_n
            discounted_cumulative_sum = np.cumsum(recorded_intrinsic_rews * args.int_gamma**np.arange(len(recorded_intrinsic_rews)))
            discounted_cumulative_lineplot = sns.lineplot(discounted_cumulative_sum)
            log_data = wandb.Image(fig)
            wandb.log({"int_rewards_plot/discounted_cumulative_rewards": log_data}, step=global_step)
            plt.clf()
            # Compute and log return-to-go of intrinsic rewards
            # Use np.cumsum to do it
            return_to_go = np.cumsum(recorded_intrinsic_rews[::-1])[::-1]
            return_to_go_lineplot = sns.lineplot(return_to_go)
            log_data = wandb.Image(fig)
            wandb.log({"int_rewards_plot/return_to_go": log_data}, step=global_step)
            plt.clf()
            # Compute and log discounted return-to-go of intrinsic rewards (use args.int_gamma)
            # Use np.cumsum to do it
            discounted_return_to_go = np.cumsum(recorded_intrinsic_rews[::-1] * args.int_gamma**np.arange(len(recorded_intrinsic_rews))[::-1])[::-1]
            # The above computes: gamma^n*r_n + ... + gamma^T*r_T
            # We want: gamma^0*r_n + ... + gamma^(T-n)*r_T
            # So we divide by gamma^n, where n is the index of the array
            discounted_return_to_go = discounted_return_to_go / (args.int_gamma**np.arange(len(recorded_intrinsic_rews)))
            discounted_return_to_go_lineplot = sns.lineplot(discounted_return_to_go)
            log_data = wandb.Image(fig)
            wandb.log({"int_rewards_plot/discounted_return_to_go": log_data}, step=global_step)
            plt.clf()
            # reset video logging state
            log_recorded_video = False
            frames = []
            recorded_intrinsic_rews = []

    envs.close()
    # writer.close()
    if args.save_model:
        torch.save(agent.state_dict(), os.path.join(model_path, f"model.pt"))
    gc.collect()
    torch.cuda.empty_cache()
